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
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from api.exceptions import ChainLengthExceeded, ProxyNotEntitled
from billing.entitlements import entitlement_payload, resolve_entitlement
from billing.models import Plan, Subscription, UsageLog
from core.models import Content
from proxies.models import ChainHop, Proxy, ProxyChain

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
    """Bulk-safe view of a proxy. No username, no password, no cipher."""

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
            "is_alive",
            "latency_ms",
            "last_checked",
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
    """Embedded inside a chain. Deliberately thinner than the list view."""

    class Meta:
        model = Proxy
        fields = ("id", "protocol", "host", "port", "country", "city", "tier", "is_alive")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class ChainHopSerializer(serializers.ModelSerializer):
    proxy = ProxySummarySerializer(read_only=True)

    class Meta:
        model = ChainHop
        fields = ("id", "position", "proxy")
        read_only_fields = fields


class ProxyChainSerializer(serializers.ModelSerializer):
    hops = ChainHopSerializer(many=True, read_only=True)
    proxy_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        allow_empty=False,
        max_length=ABSOLUTE_MAX_CHAIN_LENGTH,
        help_text="Ordered proxy ids. Index 0 is the first hop.",
    )
    length = serializers.SerializerMethodField()

    class Meta:
        model = ProxyChain
        fields = ("id", "name", "hops", "proxy_ids", "length", "created_at", "updated_at")
        read_only_fields = ("id", "hops", "length", "created_at", "updated_at")

    def get_length(self, obj):
        hops = getattr(obj, "_prefetched_hops", None)
        if hops is not None:
            return len(hops)
        return obj.hops.count()

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
        user = self.context["request"].user
        entitlement = resolve_entitlement(user)

        if len(set(value)) != len(value):
            raise serializers.ValidationError("A proxy may appear only once in a chain.")

        if len(value) > entitlement.max_chain_length:
            raise ChainLengthExceeded(
                detail={
                    "message": "Chain is longer than your plan allows.",
                    "requested_length": len(value),
                    "max_chain_length": entitlement.max_chain_length,
                    "plan": entitlement.plan_code,
                }
            )

        proxies = {
            proxy.pk: proxy
            for proxy in Proxy.objects.filter(pk__in=value, is_enabled=True)
        }
        missing = [pk for pk in value if pk not in proxies]
        if missing:
            raise serializers.ValidationError(
                f"Unknown or unavailable proxy id(s): {sorted(missing)}."
            )

        forbidden = [
            pk for pk in value if not entitlement.allows_tier(proxies[pk].tier)
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

        self.context["resolved_proxies"] = proxies
        return value

    def _write_hops(self, chain, proxy_ids):
        proxies = self.context.get("resolved_proxies", {})
        chain.hops.all().delete()
        ChainHop.objects.bulk_create(
            [
                ChainHop(chain=chain, proxy=proxies[pk], position=index)
                for index, pk in enumerate(proxy_ids)
            ]
        )

    @transaction.atomic
    def create(self, validated_data):
        proxy_ids = validated_data.pop("proxy_ids")
        chain = ProxyChain.objects.create(
            user=self.context["request"].user, **validated_data
        )
        self._write_hops(chain, proxy_ids)
        return chain

    @transaction.atomic
    def update(self, instance, validated_data):
        proxy_ids = validated_data.pop("proxy_ids", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if proxy_ids is not None:
            self._write_hops(instance, proxy_ids)
        return instance


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
