"""DRF serializers.

Two rules enforced here:

1. Proxy credentials never appear in a list serializer. Only
   `ProxyDetailSerializer`, served one object at a time behind a tight
   throttle, carries them.
2. Chain writes re-validate length and proxy tier against the entitlement
   resolved from the database, not against anything the client sent.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from api.exceptions import ChainLengthExceeded, ProxyNotEntitled
from billing.entitlements import entitlement_payload, resolve_entitlement
from billing.models import Plan, Subscription, UsageLog
from core.models import Content
from proxies.models import (
    ChainHop,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
)

User = get_user_model()

# Absolute ceiling regardless of plan. Latency compounds per hop; a chain
# longer than this is unusable in practice (see architecture.md section 1).
ABSOLUTE_MAX_CHAIN_LENGTH = 10


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "date_joined")
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = ("email", "password", "password_confirm", "first_name", "last_name")
        extra_kwargs = {
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
        }

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        user = User(
            email=attrs["email"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        validate_password(attrs["password"], user=user)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)

    def to_representation(self, instance):
        refresh = RefreshToken.for_user(instance)
        return {
            "user": UserSerializer(instance).data,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login. Adds the user object so the client needs one call, not two."""

    def validate(self, attrs):
        if "email" in attrs and isinstance(attrs["email"], str):
            attrs["email"] = attrs["email"].strip().lower()
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------


class ProxyListSerializer(serializers.ModelSerializer):
    """Bulk-safe view of a proxy. No username, no password, no cipher.

    The health block is here deliberately. None of it is a secret -- it is
    an aggregate of our own probe results, it says nothing about the
    credentials, and withholding it would force the app to show the
    alive/dead boolean that this whole feature exists to replace. The user
    choosing a hop is entitled to know that it drops a third of its
    connections.
    """

    protocol_display = serializers.CharField(source="get_protocol_display", read_only=True)
    requires_credentials = serializers.SerializerMethodField()

    class Meta:
        model = Proxy
        fields = (
            "id",
            "protocol",
            "protocol_display",
            "host",
            "port",
            "country",
            "city",
            "tier",
            # --- health ---------------------------------------------
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
        )
        read_only_fields = fields

    def get_requires_credentials(self, obj):
        return bool(obj.username or obj.password)


class ProxyDetailSerializer(ProxyListSerializer):
    """Single-proxy view including credentials.

    Served only by the retrieve action, which carries the
    `proxy_credentials` throttle scope and writes an audit log line.
    """

    class Meta(ProxyListSerializer.Meta):
        fields = ProxyListSerializer.Meta.fields + ("username", "password", "ss_method")
        read_only_fields = fields


class ProxySummarySerializer(serializers.ModelSerializer):
    """Embedded inside a chain or a resolution. Thinner than the list view,
    but it carries enough health to render a per-hop quality indicator
    without a second round trip."""

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
            "is_alive",
            "latency_p95_ms",
            "success_rate",
            "quality_score",
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class ChainHopSerializer(serializers.ModelSerializer):
    """A saved hop, as it reads back.

    `proxy` is null for a constrained hop -- the concrete proxy for those
    is not known until `POST /api/chains/{id}/resolve/`.
    """

    proxy = ProxySummarySerializer(read_only=True)
    is_constrained = serializers.BooleanField(read_only=True)

    class Meta:
        model = ChainHop
        fields = (
            "id",
            "position",
            "proxy",
            "is_constrained",
            "protocol",
            "country",
            "tier",
            "max_latency_ms",
            "min_success_rate",
            "allow_unprobed",
        )
        read_only_fields = fields


class ChainHopSpecSerializer(serializers.Serializer):
    """One hop on the way *in*.

    Either `proxy` (a fixed hop, the Phase 1 behaviour) or some
    constraints (a slot the resolver fills at connect time). Never both: a
    hop carrying both would silently ignore half of its own definition,
    and the database refuses it anyway.
    """

    proxy = serializers.IntegerField(
        required=False, allow_null=True, min_value=1,
        help_text="Pin this hop to one proxy. Omit for a constrained hop.",
    )
    protocol = serializers.ChoiceField(
        choices=ProxyProtocol.choices, required=False, allow_blank=True
    )
    country = serializers.CharField(
        required=False, allow_blank=True, max_length=64
    )
    tier = serializers.ChoiceField(
        choices=ProxyTier.choices, required=False, allow_blank=True
    )
    max_latency_ms = serializers.IntegerField(
        required=False, allow_null=True, min_value=1,
        help_text="Ceiling on latency_p95_ms, not on the mean.",
    )
    min_success_rate = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, max_value=1.0
    )
    allow_unprobed = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Accept proxies that have never been probed against "
        "max_latency_ms / min_success_rate. Off by default: those fields "
        "ask for a guarantee and an unprobed proxy cannot make one. Turn "
        "it on to bootstrap a freshly imported inventory rather than "
        "deleting the constraint.",
    )

    CONSTRAINT_FIELDS = (
        "protocol",
        "country",
        "tier",
        "max_latency_ms",
        "min_success_rate",
        "allow_unprobed",
    )

    def validate(self, attrs):
        constraints = {
            name: attrs.get(name)
            for name in self.CONSTRAINT_FIELDS
            # `allow_unprobed=False` is the default, not an expressed
            # constraint -- counting it as one would reject every pinned
            # hop, since the field is always present after `default`.
            if attrs.get(name) not in (None, "", False)
        }
        if attrs.get("proxy") and constraints:
            raise serializers.ValidationError(
                "A hop is either pinned to a proxy or described by "
                "constraints, not both. Constraints on a pinned hop would "
                f"be ignored: {sorted(constraints)}."
            )
        return attrs


class ProxyChainSerializer(serializers.ModelSerializer):
    """A saved chain.

    Two write shapes, exactly one per request:

    * `proxy_ids` -- the Phase 1 shape, an ordered list of fixed proxies.
      Unchanged, and every client written against it keeps working.
    * `hop_specs` -- the shape that can express a constrained hop. A mix of
      fixed and constrained hops in one chain is normal and supported.

    `hop_specs` is a separate field rather than a writable `hops` because
    `hops` is the *realised* list and has a different shape on the way out
    (it embeds the proxy, and for a constrained hop there is nothing to
    embed until resolution). Overloading one name with two shapes is how a
    client ends up POSTing what it last GET'd and getting a 400.
    """

    hops = ChainHopSerializer(many=True, read_only=True)
    proxy_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        required=False,
        allow_empty=False,
        max_length=ABSOLUTE_MAX_CHAIN_LENGTH,
        help_text="Ordered proxy ids. Index 0 is the first hop. "
        "Mutually exclusive with hop_specs.",
    )
    hop_specs = ChainHopSpecSerializer(
        many=True,
        write_only=True,
        required=False,
        allow_empty=False,
        max_length=ABSOLUTE_MAX_CHAIN_LENGTH,
        help_text="Ordered hop specifications; each is a pinned proxy or a "
        "set of constraints. Mutually exclusive with proxy_ids.",
    )
    length = serializers.SerializerMethodField()
    # Deliberately NOT an `is_resolvable` flag: answering that honestly
    # means running the resolver, and running it once per row of a list
    # response turns a cheap GET into N chain resolutions. The client asks
    # /resolve/ when it is about to connect, which is the only moment the
    # answer is true for.
    has_constrained_hops = serializers.SerializerMethodField()

    class Meta:
        model = ProxyChain
        fields = (
            "id",
            "name",
            "hops",
            "proxy_ids",
            "hop_specs",
            "length",
            "has_constrained_hops",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "hops",
            "length",
            "has_constrained_hops",
            "created_at",
            "updated_at",
        )

    def get_length(self, obj):
        hops = getattr(obj, "_prefetched_hops", None)
        if hops is not None:
            return len(hops)
        return obj.hops.count()

    def get_has_constrained_hops(self, obj):
        return any(hop.proxy_id is None for hop in obj.hops.all())

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A name is required.")
        user = self.context["request"].user
        queryset = ProxyChain.objects.filter(user=user, name__iexact=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("You already have a chain with this name.")
        return value

    def validate_proxy_ids(self, value):
        """The entitlement re-check demanded by graph.md section 4.

        Runs on create and on update. The client's own idea of its limits is
        never consulted.
        """
        if len(set(value)) != len(value):
            raise serializers.ValidationError("A proxy may appear only once in a chain.")

        entitlement = resolve_entitlement(self.context["request"].user)
        self._check_length(len(value), entitlement)
        proxies = self._check_proxies(value, entitlement)

        self.context["resolved_proxies"] = proxies
        return value

    def validate_hop_specs(self, value):
        """Same entitlement rules as `proxy_ids`, extended to constraints.

        A constrained hop cannot be checked against a concrete proxy at
        write time -- that is the point of it -- but a requested `tier`
        can be, and refusing it here is a clear error now rather than an
        unsatisfiable slot at connect time.
        """
        entitlement = resolve_entitlement(self.context["request"].user)
        self._check_length(len(value), entitlement)

        pinned = [spec["proxy"] for spec in value if spec.get("proxy")]
        if len(set(pinned)) != len(pinned):
            raise serializers.ValidationError(
                "A proxy may appear only once in a chain."
            )
        proxies = self._check_proxies(pinned, entitlement)

        forbidden_tiers = sorted(
            {
                spec["tier"]
                for spec in value
                if spec.get("tier") and not entitlement.allows_tier(spec["tier"])
            }
        )
        if forbidden_tiers:
            raise ProxyNotEntitled(
                detail={
                    "message": "One or more hops ask for a tier your plan "
                    "does not include.",
                    "allowed_proxy_tiers": list(entitlement.allowed_proxy_tiers),
                    "plan": entitlement.plan_code,
                }
            )

        self.context["resolved_proxies"] = proxies
        return value

    def validate(self, attrs):
        """Exactly one write shape, and one on create."""
        has_ids = "proxy_ids" in attrs
        has_specs = "hop_specs" in attrs

        if has_ids and has_specs:
            raise serializers.ValidationError(
                "Send proxy_ids or hop_specs, not both."
            )
        if self.instance is None and not (has_ids or has_specs):
            raise serializers.ValidationError(
                "A chain needs proxy_ids or hop_specs."
            )
        return attrs

    # -- shared entitlement checks ---------------------------------------

    @staticmethod
    def _check_length(length, entitlement):
        if length > entitlement.max_chain_length:
            raise ChainLengthExceeded(
                detail={
                    "message": "Chain is longer than your plan allows.",
                    "requested_length": length,
                    "max_chain_length": entitlement.max_chain_length,
                    "plan": entitlement.plan_code,
                }
            )

    @staticmethod
    def _check_proxies(proxy_ids, entitlement):
        if not proxy_ids:
            return {}
        proxies = {
            proxy.pk: proxy
            for proxy in Proxy.objects.filter(pk__in=proxy_ids, is_enabled=True)
        }
        missing = [pk for pk in proxy_ids if pk not in proxies]
        if missing:
            raise serializers.ValidationError(
                f"Unknown or unavailable proxy id(s): {sorted(missing)}."
            )

        forbidden = [
            pk for pk in proxy_ids if not entitlement.allows_tier(proxies[pk].tier)
        ]
        if forbidden:
            # Deliberately does not say which tier is missing, only that the
            # plan does not cover it.
            raise ProxyNotEntitled(
                detail={
                    "message": "One or more proxies are not included in your plan.",
                    "proxy_ids": sorted(forbidden),
                    "allowed_proxy_tiers": list(entitlement.allowed_proxy_tiers),
                    "plan": entitlement.plan_code,
                }
            )
        return proxies

    # -- writing ---------------------------------------------------------

    def _write_hops(self, chain, specs):
        """Replace the chain's hops. `specs` is the normalised form:
        a list of dicts, each with a `proxy` id or constraint keys."""
        proxies = self.context.get("resolved_proxies", {})
        chain.hops.all().delete()
        ChainHop.objects.bulk_create(
            [
                ChainHop(
                    chain=chain,
                    position=index,
                    proxy=proxies.get(spec.get("proxy")),
                    protocol=spec.get("protocol") or "",
                    country=(spec.get("country") or "").strip(),
                    tier=spec.get("tier") or "",
                    max_latency_ms=spec.get("max_latency_ms"),
                    min_success_rate=spec.get("min_success_rate"),
                    allow_unprobed=bool(spec.get("allow_unprobed")),
                )
                for index, spec in enumerate(specs)
            ]
        )

    @staticmethod
    def _specs_from(validated_data):
        """Normalise either write shape to one list of hop specs."""
        if "hop_specs" in validated_data:
            return validated_data.pop("hop_specs")
        if "proxy_ids" in validated_data:
            return [{"proxy": pk} for pk in validated_data.pop("proxy_ids")]
        return None

    @transaction.atomic
    def create(self, validated_data):
        specs = self._specs_from(validated_data)
        chain = ProxyChain.objects.create(
            user=self.context["request"].user, **validated_data
        )
        self._write_hops(chain, specs)
        return chain

    @transaction.atomic
    def update(self, instance, validated_data):
        specs = self._specs_from(validated_data)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if specs is not None:
            self._write_hops(instance, specs)
        return instance


class ResolvedHopSerializer(serializers.Serializer):
    """One slot of a resolved chain.

    **No credentials.** A resolve call would otherwise hand over three sets
    of paid credentials in one unthrottled request, which is precisely the
    bulk disclosure `architecture.md` section 3 forbids. The client fetches
    each hop's credentials from `credentials_url` -- one proxy per request,
    under the existing `proxy_credentials` throttle, each one audit-logged.
    Three extra round trips per connect is a cheap price for keeping the
    anti-scrape control intact.
    """

    position = serializers.IntegerField(read_only=True)
    hop_id = serializers.IntegerField(read_only=True)
    fixed = serializers.BooleanField(read_only=True)
    proxy = ProxySummarySerializer(read_only=True)
    constraints = serializers.DictField(read_only=True)
    candidate_count = serializers.IntegerField(
        read_only=True,
        help_text="Proxies that satisfied this slot. 1 means no alternative "
        "if it dies.",
    )
    failover_capable = serializers.SerializerMethodField()
    credentials_url = serializers.SerializerMethodField()

    def get_failover_capable(self, obj):
        return obj.is_failover_capable

    def get_credentials_url(self, obj):
        request = self.context.get("request")
        path = reverse("api:proxy-detail", args=[obj.proxy.pk])
        return request.build_absolute_uri(path) if request else path


# ---------------------------------------------------------------------------
# Subscription / entitlement
# ---------------------------------------------------------------------------


class PlanSerializer(serializers.ModelSerializer):
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
            "max_devices",
            "bandwidth_limit_bytes",
            "max_proxy_tier",
            "is_free_tier",
            "play_product_id",
        )
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = ("id", "plan", "started_at", "expires_at", "status", "platform", "auto_renewing")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class UsageLogSerializer(serializers.ModelSerializer):
    bytes_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = UsageLog
        fields = (
            "id",
            "bytes_up",
            "bytes_down",
            "bytes_total",
            "session_start",
            "session_end",
            "created_at",
        )
        read_only_fields = ("id", "bytes_total", "created_at")

    def validate_bytes_up(self, value):
        return self._non_negative(value)

    def validate_bytes_down(self, value):
        return self._non_negative(value)

    @staticmethod
    def _non_negative(value):
        if value < 0:
            raise serializers.ValidationError("Must not be negative.")
        return value

    def validate(self, attrs):
        start = attrs.get("session_start")
        end = attrs.get("session_end")
        now = timezone.now()
        skew = timedelta(minutes=5)
        if start and start > now + skew:
            raise serializers.ValidationError({"session_start": "Cannot be in the future."})
        if end:
            if end > now + skew:
                raise serializers.ValidationError({"session_end": "Cannot be in the future."})
            if start and end < start:
                raise serializers.ValidationError(
                    {"session_end": "Must not be before session_start."}
                )
        return attrs

    def create(self, validated_data):
        # The user comes from the token. A user_id in the body is ignored.
        return UsageLog.objects.create(
            user=self.context["request"].user, **validated_data
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["entitlements"] = entitlement_payload(self.context["request"].user)
        return data


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class ContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Content
        fields = ("id", "key", "kind", "title", "body", "priority", "published_at", "updated_at")
        read_only_fields = fields
