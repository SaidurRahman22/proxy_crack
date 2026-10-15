"""Plans, subscriptions and session usage accounting."""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimeStampedModel
from proxies.models import ProxyTier


class Plan(TimeStampedModel):
    """A purchasable (or free) tier. Every entitlement limit the server
    enforces lives here -- nothing is hardcoded in a view."""

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Stable identifier used by seeds and by the client, e.g. 'free'.",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    currency = models.CharField(max_length=3, default="USD")
    duration_days = models.PositiveIntegerField(
        default=30, help_text="0 means the plan does not expire."
    )
    max_chain_length = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Maximum hops in a saved chain. Free tier = 1.",
    )
    max_devices = models.PositiveIntegerField(default=1)
    bandwidth_limit_bytes = models.BigIntegerField(
        default=0, help_text="0 means unmetered."
    )
    max_proxy_tier = models.CharField(
        max_length=20,
        choices=ProxyTier.choices,
        default=ProxyTier.FREE,
        help_text="Highest proxy tier this plan may see. Grants everything "
        "at or below it.",
    )
    is_free_tier = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.IntegerField(default=0)
    play_product_id = models.CharField(
        max_length=200,
        blank=True,
        help_text="Google Play subscription product id. Phase 5.",
    )

    class Meta:
        ordering = ("sort_order", "price")
        constraints = [
            models.UniqueConstraint(
                fields=["play_product_id"],
                condition=~Q(play_product_id=""),
                name="uniq_play_product_id",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class SubscriptionPlatform(models.TextChoices):
    GOOGLE_PLAY = "google_play", "Google Play"
    MANUAL = "manual", "Manual / operator granted"


class Subscription(TimeStampedModel):
    """A user's claim on a plan.

    Modelled as a ForeignKey rather than a OneToOne so renewals and refunds
    keep an audit trail; a partial unique constraint still enforces the ER's
    'at most one active subscription per user'.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Null means it does not expire."
    )
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
        db_index=True,
    )
    platform = models.CharField(
        max_length=20,
        choices=SubscriptionPlatform.choices,
        default=SubscriptionPlatform.MANUAL,
    )
    platform_txn_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Google Play purchase token. Validated server-side in Phase 5.",
    )
    auto_renewing = models.BooleanField(default=False)
    last_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful receipt validation against the Play API.",
    )

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="active"),
                name="uniq_active_subscription_per_user",
            ),
            models.UniqueConstraint(
                fields=["platform_txn_id"],
                condition=~Q(platform_txn_id=""),
                name="uniq_platform_txn_id",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="sub_user_status_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} -> {self.plan_id} ({self.status})"

    def is_current(self, at=None):
        """Active and not past its expiry. The single truth test used by the
        entitlement resolver -- a stale `status` column cannot grant access."""
        at = at or timezone.now()
        if self.status != SubscriptionStatus.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > at


class UsageLog(models.Model):
    """One reported VPN session. Written by the client, so every field is
    validated server-side and the user is taken from the token, never the body."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_logs"
    )
    bytes_up = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    bytes_down = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    session_start = models.DateTimeField()
    session_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-session_start",)
        indexes = [
            models.Index(fields=["user", "session_start"], name="usage_user_start_idx"),
        ]

    def __str__(self):
        return f"{self.user_id}: up {self.bytes_up} / down {self.bytes_down}"

    @property
    def bytes_total(self):
        return self.bytes_up + self.bytes_down
