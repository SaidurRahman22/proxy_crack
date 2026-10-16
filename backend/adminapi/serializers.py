"""Serializers for the staff-only admin API.

Two rules carry over from `api.serializers` and one is new:

1. **Credentials never appear in a list.** `AdminProxyListSerializer` has no
   `username`, `password` or `ss_method`. Only `AdminProxyDetailSerializer`
   -- used by retrieve/create/update, one row at a time -- carries them.
2. **Password hashes never leave the server.** `password` on the user
   serializer is `write_only`; the hash is not a field at all.
3. Writable relations are accepted as `user_id` / `plan_id` *and* as a bare
   `user` / `plan` integer, because the contract describes those keys as
   nested read objects and says nothing about the write shape. Accepting
   both removes a whole class of SPA integration failure.
"""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from adminapi.exceptions import PrivilegeEscalationDenied
from adminapi.filters import MAX_IDS
from adminapi.models import AdminAuditLog
from billing.models import (
    Plan,
    Subscription,
    SubscriptionStatus,
    UsageLog,
)
from core.models import Content
from proxies.models import ChainHop, Proxy, ProxyChain, ProxyProtocol, UploadBatch

User = get_user_model()

# Writable only by a superuser, and never by the account they describe.
PRIVILEGE_FIELDS = ("is_staff", "is_superuser")

# "line 12: port 'abc' is not a number" as written by proxies.importers.
_ERROR_LINE_RE = re.compile(r"^line\s+(\d+):\s*(.*)$")


def parse_batch_errors(errors_text):
    """`UploadBatch.errors` (newline-joined text) -> `[{line, reason}]`.

    The importer stores a flat text blob so a batch row stays greppable.
    The SPA renders a per-line error report, so the structure is rebuilt
    here rather than changing the importer's storage format -- the mobile
    side and the management command read that column too.
    """
    rows = []
    for raw in (errors_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _ERROR_LINE_RE.match(line)
        if match:
            rows.append({"line": int(match.group(1)), "reason": match.group(2)})
        else:
            # e.g. the "... further errors truncated" marker.
            rows.append({"line": None, "reason": line})
    return rows


# ---------------------------------------------------------------------------
# Shared nested summaries
# ---------------------------------------------------------------------------


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email")
        read_only_fields = fields


class PlanSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ("id", "code", "name")
        read_only_fields = fields


class SubscriptionSummarySerializer(serializers.ModelSerializer):
    """The `subscription` block nested in a user row."""

    plan = PlanSummarySerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = ("id", "plan", "status", "started_at", "expires_at")
        read_only_fields = fields


class ProxySummarySerializer(serializers.ModelSerializer):
    """The proxy inside a chain hop. No credentials, by construction."""

    class Meta:
        model = Proxy
        fields = ("id", "protocol", "host", "country")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AdminMeSerializer(serializers.ModelSerializer):
    """`GET /api/admin/auth/me/` and the `user` block in a login response."""

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "is_staff", "is_superuser")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class AdminUserSerializer(serializers.ModelSerializer):
    """Full user CRUD, with a privilege boundary *between* staff.

    The contract lists `is_staff` and `is_superuser` as ordinary CRUD
    fields. Taken literally that makes staff equal to superuser: any staff
    account can `PATCH {"is_superuser": true}` onto itself and gain Django
    Admin at `/admin/`, which is a far broader write surface than this API.
    So:

    * **Only a superuser may change a privilege flag.** Enforced in
      `get_fields` (the flags become read-only), which keeps a round-trip
      PUT of unchanged values working for ordinary staff, *and* in
      `validate`, which refuses loudly when the submitted value actually
      differs -- a silently ignored privilege change is not an answer.
    * **Nobody may change their own**, superuser included. That closes
      self-escalation and, incidentally, the last-superuser lockout.

    `password` is write-only and **create-only**. Changing a password is a
    deliberate act with its own endpoint, its own privilege rules and its
    own audit event; allowing it to ride along in a profile PATCH would
    route the highest-consequence write on this resource through the
    lowest-ceremony path. Omitting it on create leaves the account with an
    unusable password -- Django's own semantic for
    `create_user(password=None)` -- rather than inventing a placeholder.

    `groups` and `user_permissions` are deliberately absent: they are a
    second, quieter escalation path than `is_superuser`, and the field list
    here is explicit (never `__all__`) so a future model field cannot add
    itself to the API.
    """

    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        style={"input_type": "password"},
    )
    # Declared explicitly so `get_fields` can make them conditionally
    # read-only. DRF ignores `Meta.read_only_fields` for declared fields, so
    # the mechanism has to be the field object itself.
    is_staff = serializers.BooleanField(required=False)
    is_superuser = serializers.BooleanField(required=False)
    subscription = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            "subscription",
            "password",
        )
        read_only_fields = ("id", "date_joined", "last_login", "subscription")
        extra_kwargs = {
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
        }

    def _requester(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def _is_superuser_request(self):
        requester = self._requester()
        return bool(requester and requester.is_authenticated and requester.is_superuser)

    def get_fields(self):
        fields = super().get_fields()
        if not self._is_superuser_request():
            for name in PRIVILEGE_FIELDS:
                fields[name].read_only = True
        return fields

    def get_subscription(self, obj):
        """The user's current subscription summary, or null.

        Reads the `active_subscriptions` attribute the viewset prefetches so
        a 200-row page costs one extra query, not 200.
        """
        prefetched = getattr(obj, "active_subscriptions", None)
        if prefetched is not None:
            subscription = prefetched[0] if prefetched else None
        else:
            subscription = (
                obj.subscriptions.filter(status=SubscriptionStatus.ACTIVE)
                .select_related("plan")
                .order_by("-started_at")
                .first()
            )
        if subscription is None:
            return None
        return SubscriptionSummarySerializer(subscription).data

    def validate_email(self, value):
        value = (value or "").strip().lower()
        queryset = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("An account with this email exists.")
        return value

    def validate_password(self, value):
        if self.instance is not None:
            raise serializers.ValidationError(
                "Passwords are changed through "
                "POST /api/admin/users/{id}/set_password/, which applies the "
                "privilege rules and writes an audit event."
            )
        # Validated against the eventual user so the similarity validator
        # can actually see the email it is comparing against.
        candidate = User(
            email=self.initial_data.get("email", "") or "",
            first_name=self.initial_data.get("first_name", "") or "",
            last_name=self.initial_data.get("last_name", "") or "",
        )
        validate_password(value, user=candidate)
        return value

    def validate(self, attrs):
        """Refuse a privilege change loudly rather than dropping it.

        `get_fields` already makes the flags read-only for non-superusers,
        which means DRF would simply discard them -- fine for an unchanged
        round-trip, wrong for a deliberate attempt. This re-reads the raw
        submission so an actual attempt gets a 403 and an audit trail
        instead of a 200 that quietly did nothing.
        """
        raw = self.initial_data if hasattr(self.initial_data, "get") else {}
        requester = self._requester()
        coercer = serializers.BooleanField()

        for name in PRIVILEGE_FIELDS:
            if name not in raw:
                continue
            try:
                submitted = coercer.to_internal_value(raw[name])
            except serializers.ValidationError:
                raise serializers.ValidationError({name: "Must be a boolean."})

            current = bool(getattr(self.instance, name, False))
            if submitted == current:
                continue

            if self.instance is not None and requester is not None:
                if self.instance.pk == requester.pk:
                    raise PrivilegeEscalationDenied(
                        {
                            "message": (
                                "You cannot change your own privilege flags. "
                                "Another superuser must make this change."
                            ),
                            "field": name,
                        }
                    )

            if not self._is_superuser_request():
                raise PrivilegeEscalationDenied(
                    {
                        "message": (
                            "Only a superuser may grant or revoke staff and "
                            "superuser access."
                        ),
                        "field": name,
                    }
                )

        # Deactivating a superuser is not escalation, but it is the way to
        # remove the only account that could stop an abusive staff member.
        if (
            self.instance is not None
            and self.instance.is_superuser
            and "is_active" in attrs
            and attrs["is_active"] != self.instance.is_active
            and not self._is_superuser_request()
        ):
            raise PrivilegeEscalationDenied(
                {
                    "message": "Only a superuser may deactivate a superuser.",
                    "field": "is_active",
                }
            )

        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class SetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(style={"input_type": "password"})

    def validate_password(self, value):
        validate_password(value, user=self.context.get("target_user"))
        return value


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


class AdminPlanSerializer(serializers.ModelSerializer):
    """`description` and `currency` are beyond the contract's field list but
    are served by the mobile `/api/subscription/`; without them the operator
    could not edit copy the app already displays."""

    active_subscription_count = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = (
            "id",
            "code",
            "name",
            "description",
            "price",
            "currency",
            "duration_days",
            "max_chain_length",
            "max_proxy_tier",
            "max_devices",
            "bandwidth_limit_bytes",
            "is_free_tier",
            "is_active",
            "sort_order",
            "play_product_id",
            "active_subscription_count",
        )
        read_only_fields = ("id", "active_subscription_count")

    def get_active_subscription_count(self, obj):
        annotated = getattr(obj, "active_subscription_count_annotated", None)
        if annotated is not None:
            return annotated
        return obj.subscriptions.filter(status=SubscriptionStatus.ACTIVE).count()


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class AdminSubscriptionSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    plan = PlanSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source="user", write_only=True
    )
    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=Plan.objects.all(), source="plan", write_only=True
    )

    class Meta:
        model = Subscription
        fields = (
            "id",
            "user",
            "plan",
            "user_id",
            "plan_id",
            "started_at",
            "expires_at",
            "status",
            "platform",
            "platform_txn_id",
            "auto_renewing",
            "last_verified_at",
        )
        read_only_fields = ("id", "user", "plan")

    def to_internal_value(self, data):
        """Accept `{"user": 3}` as well as `{"user_id": 3}`.

        `user` and `plan` are read-only nested objects on the way out, so a
        SPA that round-trips a row would otherwise have to remember to
        rename two keys before PUTting it back.
        """
        if hasattr(data, "copy"):
            data = data.copy()
            for nested, write_key in (("user", "user_id"), ("plan", "plan_id")):
                value = data.get(nested)
                if write_key not in data and isinstance(value, (int, str)):
                    data[write_key] = value
        return super().to_internal_value(data)

    def validate(self, attrs):
        """`uniq_active_subscription_per_user` is a partial unique index: a
        second active row raises IntegrityError, which would surface as a
        500. Caught here as a field error the SPA can render inline."""
        user = attrs.get("user") or getattr(self.instance, "user", None)
        status_value = attrs.get(
            "status", getattr(self.instance, "status", SubscriptionStatus.ACTIVE)
        )
        if user is not None and status_value == SubscriptionStatus.ACTIVE:
            clash = Subscription.objects.filter(
                user=user, status=SubscriptionStatus.ACTIVE
            )
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {
                        "status": (
                            "This user already has an active subscription. "
                            "Cancel or expire it first."
                        )
                    }
                )

        started = attrs.get("started_at", getattr(self.instance, "started_at", None))
        expires = attrs.get("expires_at", getattr(self.instance, "expires_at", None))
        if started and expires and expires <= started:
            raise serializers.ValidationError(
                {"expires_at": "Must be after started_at."}
            )
        return attrs


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------


class AdminProxyListSerializer(serializers.ModelSerializer):
    """Bulk-safe. No username, no password, no cipher -- ever.

    `requires_credentials` is the boolean the operator actually needs in a
    table: whether this row carries credentials, without transporting them.

    The health block rides on the list because it is not a secret -- it is
    an aggregate of our own probe results -- and because a health dashboard
    that needs a detail request per row is not a dashboard. Every one of
    these fields is read-only: they are derived from `ProxyProbe` by
    `proxies.health`, and an operator editing `success_rate` by hand would
    be overwritten by the next probe run anyway.
    """

    requires_credentials = serializers.SerializerMethodField()

    class Meta:
        model = Proxy
        fields = (
            "id",
            "protocol",
            "host",
            "port",
            "country",
            "city",
            "tier",
            "is_enabled",
            # --- health, all derived -------------------------------
            "is_alive",
            "latency_ms",
            "last_checked",
            "success_rate",
            "probe_count",
            "consecutive_failures",
            "latency_p50_ms",
            "latency_p95_ms",
            "quality_score",
            "requires_credentials",
            "upload_batch",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "requires_credentials",
            "created_at",
            "updated_at",
            "success_rate",
            "probe_count",
            "consecutive_failures",
            "latency_p50_ms",
            "latency_p95_ms",
            "quality_score",
        )

    def get_requires_credentials(self, obj):
        return bool(obj.username or obj.password)

    def validate_host(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A host is required.")
        return value

    def validate(self, attrs):
        protocol = attrs.get("protocol", getattr(self.instance, "protocol", None))
        ss_method = attrs.get("ss_method", getattr(self.instance, "ss_method", ""))
        if protocol == ProxyProtocol.SHADOWSOCKS and not (ss_method or "").strip():
            raise serializers.ValidationError(
                {"ss_method": "A cipher is required for protocol=ss."}
            )

        # uniq_proxy_endpoint is a database constraint; checking it here
        # turns a 500 into a field error.
        host = attrs.get("host", getattr(self.instance, "host", None))
        port = attrs.get("port", getattr(self.instance, "port", None))
        if protocol and host and port:
            clash = Proxy.objects.filter(protocol=protocol, host=host, port=port)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"host": f"{protocol}://{host}:{port} already exists."}
                )
        return attrs


class AdminProxyDetailSerializer(AdminProxyListSerializer):
    """One proxy, credentials decrypted.

    Served only by retrieve/create/update. `ProxyViewSet.retrieve` writes the
    `credential_access` audit line before this renders.
    """

    class Meta(AdminProxyListSerializer.Meta):
        fields = AdminProxyListSerializer.Meta.fields + (
            "username",
            "password",
            "ss_method",
        )
        extra_kwargs = {
            "username": {"required": False, "allow_blank": True},
            "password": {"required": False, "allow_blank": True},
            "ss_method": {"required": False, "allow_blank": True},
        }


class _BulkIdsSerializer(serializers.Serializer):
    """`ids[]` in a request body.

    Capped at the same `MAX_IDS` as the `?ids=` query filter. The filter
    backend's cap does not apply here -- a body does not pass through it --
    and an uncapped list is one request that deletes the inventory.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=MAX_IDS,
    )

    def validate_ids(self, value):
        # De-duplicated so `{deleted: n}` matches what the caller asked for.
        return list(dict.fromkeys(value))


class BulkEnableSerializer(_BulkIdsSerializer):
    is_enabled = serializers.BooleanField()


class BulkDeleteSerializer(_BulkIdsSerializer):
    pass


class ProxyCSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class ProbeRequestSerializer(serializers.Serializer):
    """`POST /api/admin/proxies/probe/` -- "check these now".

    Capped far below `MAX_IDS` because this endpoint probes
    **synchronously in the request worker**. Fifty proxies against a ten
    second timeout is already most of a minute of held worker in the worst
    case, and a thousand would be a self-inflicted outage. The scheduled
    `probe_proxies` command is the bulk path; this is the button beside a
    filtered table.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False
    )

    def validate_ids(self, value):
        cap = int(getattr(settings, "PROXY_PROBE_API_MAX_IDS", 50))
        unique = list(dict.fromkeys(value))
        if len(unique) > cap:
            raise serializers.ValidationError(
                f"At most {cap} proxies per probe run; this endpoint probes "
                f"synchronously. Use the probe_proxies management command "
                f"for anything larger."
            )
        return unique


# ---------------------------------------------------------------------------
# Upload batches
# ---------------------------------------------------------------------------


class AdminUploadBatchSerializer(serializers.ModelSerializer):
    uploaded_by = serializers.SerializerMethodField()
    errors = serializers.SerializerMethodField()
    errors_text = serializers.SerializerMethodField()

    class Meta:
        model = UploadBatch
        fields = (
            "id",
            "filename",
            "uploaded_by",
            "row_count",
            "success_count",
            "created_count",
            "updated_count",
            "fail_count",
            "errors",
            "errors_text",
            "created_at",
        )
        read_only_fields = fields

    def get_uploaded_by(self, obj):
        return obj.uploaded_by.email if obj.uploaded_by_id else None

    def get_errors(self, obj):
        return parse_batch_errors(obj.errors)

    def get_errors_text(self, obj):
        """The raw column, kept alongside the parsed list so an operator can
        read an error the parser did not recognise."""
        return obj.errors or ""


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class AdminContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Content
        fields = (
            "id",
            "key",
            "kind",
            "title",
            "body",
            "priority",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


# ---------------------------------------------------------------------------
# Usage logs
# ---------------------------------------------------------------------------


class AdminUsageLogSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    bytes_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = UsageLog
        fields = (
            "id",
            "user",
            "bytes_up",
            "bytes_down",
            "bytes_total",
            "session_start",
            "session_end",
            "created_at",
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class AdminChainHopSerializer(serializers.ModelSerializer):
    proxy = ProxySummarySerializer(read_only=True)

    class Meta:
        model = ChainHop
        fields = ("id", "position", "proxy")
        read_only_fields = fields


class AdminChainSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    hops = AdminChainHopSerializer(many=True, read_only=True)
    length = serializers.SerializerMethodField()

    class Meta:
        model = ProxyChain
        fields = ("id", "user", "name", "hops", "length", "created_at", "updated_at")
        read_only_fields = fields

    def get_length(self, obj):
        return len(obj.hops.all())


# ---------------------------------------------------------------------------
# Audit log (read-only)
# ---------------------------------------------------------------------------


class AdminAuditLogSerializer(serializers.ModelSerializer):
    """One audit row, as the console reads it.

    `actor` is the live FK and goes null when the account is deleted;
    `actor_email` is the copy taken at write time and does not. Both are
    exposed deliberately -- a row where `actor` is null and `actor_email`
    is set is not missing data, it is the record of someone whose account
    has since been removed, and that distinction is the point of keeping
    two columns.

    Every field is read-only. The viewset is read-only too, and the model
    refuses writes, and the database refuses them under that -- this is
    simply the outermost of the four.
    """

    actor = UserSummarySerializer(read_only=True)
    actor_deleted = serializers.SerializerMethodField()

    class Meta:
        model = AdminAuditLog
        fields = (
            "id",
            "action",
            "actor",
            "actor_email",
            "actor_deleted",
            "target_type",
            "target_id",
            "changes",
            "payload",
            "ip",
            "user_agent",
            "created_at",
        )
        read_only_fields = fields

    def get_actor_deleted(self, obj):
        """True when the row outlived the account that made it."""
        return obj.actor_id is None and bool(obj.actor_email)
