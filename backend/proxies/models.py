"""Proxy inventory, bulk-upload batches, and user-saved proxy chains."""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.fields import EncryptedCharField
from core.models import TimeStampedModel


class ProxyProtocol(models.TextChoices):
    SHADOWSOCKS = "ss", "Shadowsocks"
    SOCKS5 = "socks5", "SOCKS5"
    HTTP = "http", "HTTP"


class ProxyTier(models.TextChoices):
    """Access tier of a proxy. Ordered: free is the lowest.

    A plan grants everything up to and including its `max_proxy_tier`, so a
    premium plan sees free proxies too but never the reverse.
    """

    FREE = "free", "Free"
    PREMIUM = "premium", "Premium"


# Ordering used by the entitlement resolver. Add new tiers here with a rank.
TIER_RANK = {
    ProxyTier.FREE: 0,
    ProxyTier.PREMIUM: 1,
}


def tiers_up_to(tier):
    """Every tier at or below `tier`, as a tuple of raw values."""
    ceiling = TIER_RANK.get(tier, 0)
    return tuple(str(value) for value, rank in TIER_RANK.items() if rank <= ceiling)


class UploadBatch(models.Model):
    """One CSV bulk upload of proxies, with its success/fail tally.

    Kept as a first-class row so a bad import can be audited and the proxies
    it created can be found and removed.
    """

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="upload_batches",
    )
    filename = models.CharField(max_length=255)
    row_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    fail_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    errors = models.TextField(
        blank=True,
        help_text="Per-row rejection reasons, one per line.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name_plural = "upload batches"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.filename} ({self.success_count}/{self.row_count} ok)"


class Proxy(TimeStampedModel):
    """A single upstream proxy endpoint.

    `username` / `password` are the operator's paid credentials and are the
    product. They are never serialised into a list response -- see
    `api.serializers.ProxyListSerializer`.
    """

    protocol = models.CharField(
        max_length=10, choices=ProxyProtocol.choices, db_index=True
    )
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    # Encrypted at rest -- see core/crypto.py. These are not filterable or
    # searchable; the ciphertext differs on every write.
    username = EncryptedCharField(max_length=255, blank=True)
    password = EncryptedCharField(max_length=255, blank=True)
    ss_method = EncryptedCharField(
        max_length=64,
        blank=True,
        verbose_name="Shadowsocks cipher",
        help_text="Required for protocol=ss, e.g. aes-256-gcm. Ignored otherwise.",
    )
    country = models.CharField(max_length=64, blank=True, db_index=True)
    city = models.CharField(max_length=64, blank=True)
    tier = models.CharField(
        max_length=20,
        choices=ProxyTier.choices,
        default=ProxyTier.PREMIUM,
        db_index=True,
        help_text="Entitlement gate. Defaults to premium so a mis-tagged "
        "import is never over-exposed.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Operator switch. A disabled proxy is never served, "
        "regardless of health.",
    )
    is_alive = models.BooleanField(default=True, db_index=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    upload_batch = models.ForeignKey(
        UploadBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proxies",
    )

    class Meta:
        verbose_name_plural = "proxies"
        ordering = ("country", "city", "host")
        constraints = [
            models.UniqueConstraint(
                fields=["protocol", "host", "port"],
                name="uniq_proxy_endpoint",
            ),
        ]
        indexes = [
            models.Index(fields=["tier", "is_enabled", "is_alive"], name="proxy_scope_idx"),
            models.Index(fields=["country", "protocol"], name="proxy_geo_proto_idx"),
        ]

    def __str__(self):
        return f"{self.protocol}://{self.host}:{self.port}"

    @property
    def endpoint(self):
        return f"{self.host}:{self.port}"


class ProxyChain(TimeStampedModel):
    """A user's saved ordered chain of proxies (a preset)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chains"
    )
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="uniq_chain_name_per_user"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.user_id})"

    @property
    def length(self):
        return self.hops.count()


class ChainHop(models.Model):
    """One position in a chain. Order is explicit; it decides the `detour`
    wiring the engine builds, so it is never left to insertion order."""

    chain = models.ForeignKey(
        ProxyChain, on_delete=models.CASCADE, related_name="hops"
    )
    proxy = models.ForeignKey(
        Proxy, on_delete=models.PROTECT, related_name="chain_hops"
    )
    position = models.PositiveIntegerField(help_text="0-based hop index.")

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["chain", "position"], name="uniq_hop_position_per_chain"
            ),
            models.UniqueConstraint(
                fields=["chain", "proxy"], name="uniq_proxy_per_chain"
            ),
        ]

    def __str__(self):
        return f"hop {self.position} -> {self.proxy}"
