"""Proxy inventory, bulk-upload batches, and user-saved proxy chains."""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.fields import EncryptedCharField
from core.models import TimeStampedModel

# The quality score given to a proxy nothing has probed yet.
#
# Deliberately mid-scale: an unprobed proxy must rank *below* one that has
# proved itself and *above* one that has proved it fails, because "unknown"
# is genuinely between the two. Zero would make a fresh CSV import
# unselectable until the first probe run; 100 would make it beat everything
# the prober has actually measured.
#
# `settings.PROXY_UNPROBED_SCORE` mirrors this value and is what
# `proxies.health` reads. This constant exists only because a model field
# default has to be a literal migrations can serialise.
UNPROBED_QUALITY_SCORE = 50.0


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
    # ---- Health -------------------------------------------------------
    # Every field below is DERIVED from `ProxyProbe` rows by
    # `proxies.health.recompute_health`. Nothing else should write them:
    # they are a denormalised cache of the probe table, and the recompute
    # is a pure function of those rows, which is what makes two concurrent
    # probe runs safe (see proxies/health.py).
    is_alive = models.BooleanField(
        default=True,
        db_index=True,
        help_text="False once `consecutive_failures` reaches "
        "settings.PROXY_DEAD_AFTER_FAILURES. One failed probe is noise, "
        "not death.",
    )
    latency_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Latency of the most recent SUCCESSFUL probe. Kept for "
        "the Phase 1 API contract; latency_p95_ms is the field to rank on.",
    )
    last_checked = models.DateTimeField(null=True, blank=True)
    success_rate = models.FloatField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Successes / probes over the rolling window (0.0-1.0), "
        "NOT lifetime. Null until the proxy has been probed at least once.",
    )
    probe_count = models.PositiveIntegerField(
        default=0,
        help_text="Probes inside the current rolling window -- the sample "
        "`success_rate` is computed from. A 100%% rate over 2 probes and "
        "over 200 are not the same claim, and the scorer treats them "
        "differently.",
    )
    consecutive_failures = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Trailing failures in the probe history. Reset to 0 by "
        "any success. This, not the last probe, decides `is_alive`.",
    )
    latency_p50_ms = models.PositiveIntegerField(null=True, blank=True)
    latency_p95_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="95th percentile over the window. A proxy with a good "
        "mean and a terrible tail is a bad hop, and a mean hides that.",
    )
    quality_score = models.FloatField(
        default=UNPROBED_QUALITY_SCORE,
        db_index=True,
        help_text="0-100. Reliability is primary, latency secondary -- see "
        "proxies.health.compute_quality_score. Stored rather than computed "
        "so the resolver can rank candidates in SQL.",
    )
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
            # The resolver's hot path: scope by entitlement + health, then
            # rank. Ordering the index by -quality_score lets the "best
            # healthy candidate" query be an index scan with a limit.
            models.Index(
                fields=["is_enabled", "is_alive", "tier", "-quality_score"],
                name="proxy_resolve_idx",
            ),
        ]

    def __str__(self):
        return f"{self.protocol}://{self.host}:{self.port}"

    @property
    def endpoint(self):
        return f"{self.host}:{self.port}"

    @property
    def is_probed(self):
        """Whether any probe has been recorded inside the rolling window."""
        return self.probe_count > 0


class ProbeErrorKind(models.TextChoices):
    """Why a probe failed.

    Deliberately a small, closed set. The point is to make "is this proxy
    dead, or is our own DNS broken" answerable from an aggregate query, so
    the categories are the ones that imply different operator actions --
    not a free-text field that becomes 400 distinct strings.
    """

    TIMEOUT = "timeout", "Timed out"
    REFUSED = "refused", "Connection refused"
    AUTH_FAILED = "auth_failed", "Authentication rejected"
    DNS = "dns", "Name resolution failed"
    TLS = "tls", "TLS failure"
    PROTOCOL = "protocol", "Protocol error"
    # Shadowsocks only, and the protocol's fault rather than ours. The
    # server never replies to anything it cannot decrypt, so a wrong
    # password and a server that has silently stopped forwarding produce
    # byte-for-byte the same silence. A probe that picked `auth_failed`
    # or `timeout` here would be guessing; this value says so out loud.
    # See proxies.probing.SHADOWSOCKS_SILENCE.
    UNVERIFIABLE = "unverifiable", "No reply; cause indeterminate"
    UNKNOWN = "unknown", "Unknown"


class ProxyProbe(models.Model):
    """One health observation of one proxy.

    This is the only table in the schema that grows without a user action,
    so it is also the only one with a retention policy: see the
    `prune_probes` management command and
    `settings.PROXY_PROBE_RETENTION_DAYS`. At one probe per proxy per five
    minutes, 10k proxies produce ~2.9M rows a day -- unbounded growth here
    is a production outage, not an untidiness.

    Rows are append-only. Nothing updates a probe after it is written, so
    two probe runs racing each other produce two honest observations rather
    than a corrupted one.
    """

    proxy = models.ForeignKey(
        Proxy, on_delete=models.CASCADE, related_name="probes"
    )
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)
    succeeded = models.BooleanField()
    latency_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Round trip of a full CONNECT through the proxy to the "
        "target. Null on failure -- a failed probe has no latency, and "
        "storing the timeout duration there would poison the percentiles.",
    )
    error_kind = models.CharField(
        max_length=20,
        choices=ProbeErrorKind.choices,
        blank=True,
        help_text="Empty on success.",
    )
    target = models.CharField(
        max_length=120,
        blank=True,
        help_text="host:port the probe CONNECTed to. Recorded per row "
        "because when the target itself goes down, every proxy looks dead "
        "and this column is the only way to tell.",
    )

    class Meta:
        ordering = ("-checked_at", "-pk")
        verbose_name = "proxy probe"
        verbose_name_plural = "proxy probes"
        indexes = [
            # The rolling-window query: newest N probes for one proxy.
            models.Index(
                fields=["proxy", "-checked_at"], name="probe_proxy_time_idx"
            ),
            # Aggregates over a run ("how many auth failures in the last
            # hour"), and the prune scan.
            models.Index(
                fields=["checked_at", "succeeded"], name="probe_time_ok_idx"
            ),
        ]

    def __str__(self):
        state = "ok" if self.succeeded else (self.error_kind or "failed")
        return f"{self.proxy_id} {state} @ {self.checked_at:%Y-%m-%d %H:%M:%S}"


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
    """One position in a chain. Order is explicit; it decides the
    `dialerProxy` wiring the engine builds, so it is never left to
    insertion order.

    A hop is one of two things:

    **Fixed** -- `proxy` is set. The chain always uses exactly that proxy.
    This is proxychains' model and the only one Phase 1 had.

    **Constrained** -- `proxy` is null and the remaining fields describe
    what would be acceptable here: "position 2 = any premium SOCKS5 in NL,
    p95 under 200 ms, success rate at least 0.95". The concrete proxy is
    chosen at connect time by `proxies.resolution.resolve_chain`, so a hop
    dying re-resolves the slot instead of killing the tunnel.

    The two are mutually exclusive and a database constraint says so. A hop
    carrying both would silently ignore half of its own definition, which
    is the kind of thing that is discovered months later in a support
    ticket.

    A blank constraint field means "no opinion", not "must be blank": a
    fully-blank constrained hop resolves to the best healthy proxy the
    user's plan allows.
    """

    chain = models.ForeignKey(
        ProxyChain, on_delete=models.CASCADE, related_name="hops"
    )
    proxy = models.ForeignKey(
        Proxy,
        on_delete=models.PROTECT,
        related_name="chain_hops",
        null=True,
        blank=True,
        help_text="Set for a fixed hop; null for a constrained one.",
    )
    position = models.PositiveIntegerField(help_text="0-based hop index.")

    # ---- Constraints (constrained hops only) ---------------------------
    protocol = models.CharField(
        max_length=10,
        choices=ProxyProtocol.choices,
        blank=True,
        help_text="Blank means any. Note an http hop makes the whole chain "
        "TCP-only (architecture.md section 1).",
    )
    country = models.CharField(
        max_length=64, blank=True, help_text="Blank means any. Matched "
        "case-insensitively against Proxy.country."
    )
    tier = models.CharField(
        max_length=20,
        choices=ProxyTier.choices,
        blank=True,
        help_text="Blank means any tier the plan allows. Never widens "
        "entitlement -- the resolver intersects this with the plan.",
    )
    max_latency_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Ceiling on latency_p95_ms, not on the mean. An unprobed "
        "proxy has no p95 and therefore cannot satisfy this.",
    )
    min_success_rate = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Floor on the rolling success rate, 0.0-1.0. An unprobed "
        "proxy has no rate and therefore cannot satisfy this.",
    )
    allow_unprobed = models.BooleanField(
        default=False,
        help_text="Let never-probed proxies satisfy max_latency_ms / "
        "min_success_rate anyway. Off by default, because the whole point "
        "of those fields is a guarantee and an unprobed proxy cannot make "
        "one. Turn it on to bootstrap a freshly imported inventory, where "
        "every candidate is unprobed and the hop would otherwise resolve "
        "to nothing until the first `probe_proxies` run.",
    )

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["chain", "position"], name="uniq_hop_position_per_chain"
            ),
            # Conditioned on a non-null proxy so the rule does not depend on
            # how a given backend treats NULLs in a unique index: several
            # constrained hops per chain are normal and must not collide.
            models.UniqueConstraint(
                fields=["chain", "proxy"],
                condition=Q(proxy__isnull=False),
                name="uniq_proxy_per_chain",
            ),
            models.CheckConstraint(
                condition=(
                    Q(proxy__isnull=True)
                    | Q(
                        protocol="",
                        country="",
                        tier="",
                        max_latency_ms__isnull=True,
                        min_success_rate__isnull=True,
                        # `allow_unprobed` is a constraint field like the
                        # rest: on a pinned hop it would describe a
                        # selection that never happens.
                        allow_unprobed=False,
                    )
                ),
                name="hop_is_fixed_or_constrained",
            ),
        ]

    def __str__(self):
        if self.proxy_id:
            return f"hop {self.position} -> {self.proxy}"
        return f"hop {self.position} -> {self.describe_constraints()}"

    @property
    def is_constrained(self):
        return self.proxy_id is None

    def describe_constraints(self):
        """Human-readable constraint summary, used in resolution errors."""
        parts = []
        if self.protocol:
            parts.append(self.protocol)
        if self.country:
            parts.append(f"country={self.country}")
        if self.tier:
            parts.append(f"tier={self.tier}")
        if self.max_latency_ms is not None:
            parts.append(f"p95<={self.max_latency_ms}ms")
        if self.min_success_rate is not None:
            parts.append(f"success>={self.min_success_rate:.2f}")
        if self.allow_unprobed and self.has_health_thresholds:
            parts.append("or unprobed")
        return "any healthy proxy" if not parts else " ".join(parts)

    @property
    def has_health_thresholds(self):
        """Whether this hop asks for a guarantee only a probe can give.

        `allow_unprobed` is meaningful only alongside one of these: with
        no threshold there is nothing for an unprobed proxy to fail, so
        the flag is a no-op rather than an error.
        """
        return self.max_latency_ms is not None or self.min_success_rate is not None

    def constraint_dict(self):
        """The constraints as a plain dict, for API responses and errors."""
        return {
            "protocol": self.protocol or None,
            "country": self.country or None,
            "tier": self.tier or None,
            "max_latency_ms": self.max_latency_ms,
            "min_success_rate": self.min_success_rate,
            "allow_unprobed": self.allow_unprobed,
        }
