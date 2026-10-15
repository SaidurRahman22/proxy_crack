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

KNOWN GAP (reported, not fixed here): these are log records, not rows in an
append-only table, and they go wherever `LOGGING` sends them -- by default
stdout. A staff account can read them and a superuser can delete them.
`AdminAuditLog` as a durable model is the right next step.
"""

import json
import logging

logger = logging.getLogger("proxycrack.audit")

__all__ = ["audit", "changed_fields", "client_ip"]

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


def audit(event, request=None, level=logging.INFO, **fields):
    """Emit one audit event.

    `event` is the stable name; everything else is payload. Never pass a
    credential, a password or a token.
    """
    actor = getattr(request, "user", None)
    payload = {
        "event": event,
        "actor_id": getattr(actor, "pk", None),
        # Denormalised: the user row may be deleted later, and an audit
        # trail that resolves to a dangling id is not a trail.
        "actor_email": getattr(actor, "email", None),
        "ip": client_ip(request),
        "method": getattr(request, "method", None),
        "path": getattr(request, "path", None),
        **fields,
    }
    logger.log(
        level,
        "%s %s",
        event,
        json.dumps(payload, default=str, sort_keys=True),
        extra={"audit": payload},
    )
    return payload
