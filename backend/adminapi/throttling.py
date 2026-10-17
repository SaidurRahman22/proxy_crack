"""Rate limits for the admin API.

Two ideas shape these.

**Grade by what the endpoint can be used to take.** The generic admin read
budget has to be loose or the SPA's dense tables fight it, and a loose
budget is only safe if the endpoints that matter are not covered by it. So
credential disclosure, writes, bulk mutation and CSV import each carry their
own scope.

**Bound patience, not just bursts.** An hourly limit is defeated by waiting.
Credential disclosure therefore carries an hourly limit *and* a daily
ceiling; the daily one is what makes a full inventory scrape take months
instead of a weekend.

Every number here lives in the `default` cache. See the CACHES block in
`core/settings.py`: on a per-process backend these limits are multiplied by
the worker count and this module is decorative.
"""

import hashlib

from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

__all__ = [
    "AdminBulkThrottle",
    "AdminCredentialDailyThrottle",
    "AdminCredentialHourlyThrottle",
    "AdminCSVUploadThrottle",
    "AdminIntelThrottle",
    "AdminLoginEmailThrottle",
    "AdminLoginIPThrottle",
    "AdminProbeThrottle",
    "AdminProviderSyncThrottle",
    "AdminScopedThrottle",
]


class AdminScopedThrottle(ScopedRateThrottle):
    """`throttle_scope` on the view picks the rate."""


class AdminLoginIPThrottle(AnonRateThrottle):
    """Per source address, counting every attempt."""

    scope = "admin_login"


class AdminLoginEmailThrottle(SimpleRateThrottle):
    """Per submitted email address, counting **failures only**.

    Counting successes too would mean an operator who signs in and out
    through a working day eventually locks themselves out of their own
    console, which is how a rate limit ends up being raised to a number that
    stops limiting anything. A password guesser produces nothing but
    failures, so a failure-only counter costs the attacker everything and
    the operator nothing.

    `allow_request` deliberately does not call `throttle_success()`: the
    view records an attempt only when authentication has actually failed.
    The counter lives in the cache rather than on the instance, so the
    recording call does not need the instance that did the checking.
    """

    scope = "admin_login_email"

    def _email(self, request):
        data = getattr(request, "data", None) or {}
        try:
            email = data.get("email")
        except AttributeError:
            return None
        if not isinstance(email, str) or not email.strip():
            return None
        return email.strip().lower()

    def get_cache_key(self, request, view):
        email = self._email(request)
        if email is None:
            return None
        # Hashed so an attacker who reads the cache cannot enumerate the
        # addresses that have been tried.
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}

    def _load(self, request, view):
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return False
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        return True

    def allow_request(self, request, view):
        if self.rate is None or not self._load(request, view):
            return True
        return len(self.history) < self.num_requests

    def record_failure(self, request, view=None):
        """Called by the login view after a refused attempt."""
        if self.rate is None or not self._load(request, view):
            return
        self.history.insert(0, self.now)
        self.cache.set(self.key, self.history, self.duration)


class AdminCredentialHourlyThrottle(UserRateThrottle):
    """Bounds a burst of credential disclosures."""

    scope = "admin_proxy_credentials"


class AdminCredentialDailyThrottle(UserRateThrottle):
    """Bounds a patient one. This is the limit that actually stops a scrape:
    an attacker who respects the hourly window simply waits, and a daily
    ceiling is the only thing that makes waiting not work."""

    scope = "admin_proxy_credentials_day"


class AdminBulkThrottle(UserRateThrottle):
    scope = "admin_bulk"


class AdminCSVUploadThrottle(UserRateThrottle):
    scope = "admin_csv_upload"


class AdminIntelThrottle(UserRateThrottle):
    """Bounds operator-triggered IP intelligence lookups.

    Tighter than the probe throttle, and for a reason the probe throttle
    does not have. Probing spends **our** bandwidth against proxies we
    pay for; a lookup spends a free third party's service, and the cost of
    exhausting their patience is not a slow dashboard -- it is the whole
    feature going dark for everyone, with no way to appeal it.

    The `refresh_ip_intel` management command is the bulk path and is rate
    limited in its own right (`IP_INTEL_RATE_LIMIT_PER_MINUTE`); this
    endpoint is the "check these candidates before I sign the invoice"
    button beside it.
    """

    scope = "admin_intel"


class AdminProbeThrottle(UserRateThrottle):
    """Bounds operator-triggered probe runs.

    Unlike the other scopes here this one is not primarily an anti-abuse
    control -- it is a resource control. Each call holds a request worker
    open while it makes up to `PROXY_PROBE_API_MAX_IDS` outbound
    connections, and an operator refreshing a dashboard can exhaust the
    pool without meaning anything by it. It also bounds the outbound
    traffic we generate against a provider who may be rate-limiting us in
    turn.
    """

    scope = "admin_probe"


class AdminProviderSyncThrottle(UserRateThrottle):
    """Bounds operator-triggered provider syncs. The tightest scope here.

    Three costs stack on one press, and no other admin endpoint carries
    all three:

    * it is an authenticated, multi-page request against a vendor that
      rate limits us and can suspend the account -- and under **P4** that
      account is already the business model's weakest point;
    * it holds a request worker for the whole page walk;
    * it is the only endpoint that can disable a large slice of the
      sellable inventory in a single call.

    The scheduled `sync_webshare` command is the normal path. This is the
    "the provider says they rotated our addresses, pull it now" button,
    and six of those an hour is generous for that.
    """

    scope = "admin_provider_sync"
