"""Audit events for the admin API.

Replacing Django Admin with an SPA loses `django.contrib.admin.LogEntry`,
which today records every add, change and delete with an actor, an object
and a change message. Nothing in the contract replaces it. This module is
the minimum replacement: a single structured event stream on a dedicated
logger, so shipping it off-host is a logging-config change rather than a
code change.

Three rules:

1. **Audit the access, never the value.** A credential disclosure records
   which proxy and which actor. It never records the credential -- an audit
   log that contains the secrets is a second copy of the thing being
   protected, usually with worse access control.
2. **Structured, not formatted.** Each event carries a stable `event` name
   and a JSON payload, so "every plan entitlement change last quarter" is a
   query rather than a grep.
3. **Severity reflects investigation value, not failure.** Entitlement and
   privilege changes are logged at WARNING even though they succeeded,
   because those are the lines a human should see.

4. **Durable as well as logged.** Every event written here is also
   persisted to `adminapi.models.AdminAuditLog`, an append-only table
   (P9). The log stream stays, because shipping it off-host is still what
   turns it into evidence an attacker on this host cannot reach; the table
   is what survives a log handler nobody configured.

   Persistence happens *here*, in the one function every call site already
   goes through, rather than at each of the fifteen places that raise an
   event. Re-instrumenting those by hand would have meant that the next
   audited action is audited only if somebody remembers both halves.
"""

import json
import logging

from django.conf import settings
from django.db import DatabaseError, transaction

logger = logging.getLogger("proxycrack.audit")

__all__ = [
    "audit",
    "changed_fields",
    "client_ip",
    "scrub",
    "target_from",
    "user_agent",
]

# Changes to these are the highest-value events in the system. A single
# write to Plan.max_proxy_tier re-entitles every free account, and the
# credentials are then reachable through the *mobile* API -- touching no
# admin throttle and reading no admin detail endpoint.
PLAN_ENTITLEMENT_FIELDS = (
    "max_proxy_tier",
    "max_chain_length",
    "is_free_tier",
    "is_active",
    "bandwidth_limit_bytes",
    "max_devices",
)

USER_PRIVILEGE_FIELDS = ("is_staff", "is_superuser", "is_active")


def client_ip(request):
    """Best-effort source address.

    `X-Forwarded-For` is attacker-controlled unless a trusted proxy rewrites
    it, so the peer address is recorded alongside rather than replaced.
    """
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    peer = request.META.get("REMOTE_ADDR")
    if forwarded:
        return f"{forwarded.split(',')[0].strip()} (via {peer})"
    return peer


def changed_fields(instance, validated_data, fields):
    """`{field: {"from": old, "to": new}}` for the fields that actually moved.

    Called before `serializer.save()`, while `instance` still holds the old
    values.
    """
    changes = {}
    for field in fields:
        if field not in validated_data:
            continue
        old = getattr(instance, field, None)
        new = validated_data[field]
        if old != new:
            changes[field] = {"from": _plain(old), "to": _plain(new)}
    return changes


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def user_agent(request):
    """The client string, truncated to the column width.

    Worth recording precisely because it is attacker-controlled: it is
    weak evidence on its own and strong corroboration next to an IP and a
    timestamp when deciding whether two events came from one session.
    """
    if request is None:
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:400]


# Anything whose *key* matches one of these has its value replaced before
# the event is written, to either destination.
#
# A deny-list is normally the weaker choice, and here it is the right one
# anyway: the payloads are built by our own call sites, so the realistic
# failure is a future developer adding `password=` to an audit call, not
# an attacker smuggling a secret through a key we failed to anticipate.
# This catches that, loudly, at the seam -- and `test_audit_log.py` asserts
# it against the actual reveal endpoint rather than trusting the list.
#
# `username` is in here and `email` is not, deliberately: a *proxy's*
# username is half of a paid credential, while an actor's email is the
# identity the whole trail is built on.
SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "username",
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "private_key",
    "ss_method",
    "cipher",
)

REDACTED = "[redacted]"

# Payload keys that name what an event acted on, most specific first.
# Used to fill `target_type` / `target_id` without every call site having
# to state them -- the whole point of writing from this seam.
TARGET_HINTS = (
    ("target_user_id", "user"),
    ("proxy_id", "proxy"),
    ("plan_id", "plan"),
    ("subscription_id", "subscription"),
    ("batch_id", "upload_batch"),
)


def _is_secret_key(key):
    lowered = str(key).lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def scrub(value, _depth=0):
    """Redact anything whose key looks like a secret, recursively.

    Audit the access, never the value. A log that contains the credentials
    is a second copy of the asset, usually behind weaker access control
    than the first.

    Depth is bounded because the payloads are shallow by construction and
    an unbounded walk over caller-supplied data is a denial of service
    waiting for the one event that nests.
    """
    if _depth > 6:
        return REDACTED
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_secret_key(key) else scrub(item, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item, _depth + 1) for item in value]
    return value


def target_from(payload):
    """`(target_type, target_id)` inferred from the payload's own keys."""
    for key, kind in TARGET_HINTS:
        if payload.get(key) is not None:
            return kind, str(payload[key])[:64]
    return "", ""


def audit(event, request=None, level=logging.INFO, **fields):
    """Emit one audit event, to the log stream *and* the durable table.

    `event` is the stable name; everything else is payload. Never pass a
    credential, a password or a token -- `scrub` is a backstop for that
    rule, not a licence to break it.
    """
    actor = getattr(request, "user", None)
    actor_id = getattr(actor, "pk", None)
    actor_email = getattr(actor, "email", None)

    payload = scrub(
        {
            "event": event,
            "actor_id": actor_id,
            # Denormalised: the user row may be deleted later, and an audit
            # trail that resolves to a dangling id is not a trail.
            "actor_email": actor_email,
            "ip": client_ip(request),
            "method": getattr(request, "method", None),
            "path": getattr(request, "path", None),
            **fields,
        }
    )
    logger.log(
        level,
        "%s %s",
        event,
        json.dumps(payload, default=str, sort_keys=True),
        extra={"audit": payload},
    )
    _persist(event, payload, request, actor_id, actor_email)
    return payload


def _persist(event, payload, request, actor_id, actor_email):
    """Write the row. Never fails the request it is recording.

    The action has already happened by the time this runs, so raising
    would return a 500 for work that was done -- and would not bring the
    evidence back. A failure here is logged at CRITICAL on the audit
    logger instead, which is the channel most likely to be shipped
    off-host, so "the audit table stopped accepting writes" is itself an
    alertable event rather than a silence.

    Wrapped in its own atomic block so that a failure inside an outer
    transaction marks only this savepoint broken. Without it, an
    IntegrityError here would poison the caller's transaction and roll
    back the operation we just finished recording.
    """
    if not getattr(settings, "ADMIN_AUDIT_PERSIST", True):
        return None

    # Imported here, not at module import: `audit` is imported by
    # `adminapi.auth`, which the app registry loads while models are still
    # being set up.
    from adminapi.models import AdminAuditLog

    target_type, target_id = target_from(payload)
    changes = payload.get("changes")

    try:
        with transaction.atomic():
            return AdminAuditLog.objects.create(
                actor_id=actor_id,
                actor_email=(actor_email or "")[:254],
                action=str(event)[:64],
                target_type=target_type,
                target_id=target_id,
                changes=changes if isinstance(changes, dict) else {},
                # JSON round-tripped through the same serialisation the
                # log line uses, so a value that cannot be stored (a
                # datetime, a Decimal, a model instance) becomes a string
                # here exactly as it does there, rather than raising.
                payload=json.loads(json.dumps(payload, default=str)),
                ip=(payload.get("ip") or "")[:120],
                user_agent=user_agent(request),
            )
    except (DatabaseError, ValueError, TypeError):
        logger.critical(
            "audit_persist_failed event=%s actor=%s -- the structured log "
            "line above is the only record of this event",
            event,
            actor_id,
            exc_info=True,
        )
        return None
