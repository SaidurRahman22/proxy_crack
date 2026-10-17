"""Credential-disclosure budget for the mobile API (P16).

The defect
----------

`proxy_credentials` was a flat 60 requests/hour. D18 makes the client fetch
credentials **one hop at a time**, so a 3-hop connect costs 3 and every
failover costs up to 3 more. A session that flaps exhausts the budget in
about twenty minutes and the user is then locked out of *connecting at
all* for the rest of the hour -- precisely the conditions failover exists
to handle. It was also backwards: a paying user on a 3-hop plan burned
budget three times faster than a free user on one hop.

The fix: change the unit, not the number
----------------------------------------

The budget still allows **60 per hour**. What is counted changed from
*requests* to **distinct proxies disclosed in the window**. Re-fetching a
proxy this user was already given inside the window is free, because it
discloses nothing the client does not already hold.

That lands exactly on the line between the two populations:

* A connecting user touches `max_chain_length` proxies plus whatever
  alternates failover picks -- a handful, fetched over and over. Their
  consumption is bounded by *their chain*, not by how many times they
  reconnect. Twenty connects and twenty failovers cost four units, not
  eighty.
* A scraper needs one **new** proxy per unit of inventory. Every one of
  those is a first disclosure, so every one is charged. The wall is in
  the same place it was before this change: 60 new proxies an hour.

Nothing is spent to buy the usability back. That is the whole argument
for counting coverage instead of traffic -- a request budget cannot
separate "the same three credentials, forty times" from "forty
credentials", and those are the two cases that have to be told apart.

Bound patience as well as bursts
--------------------------------

`CredentialDailyDisclosureThrottle` adds a distinct-proxy ceiling over 24
hours, mirroring the admin API's hourly+daily pair. An hourly limit on
its own is defeated by waiting: 60/hour is 1440 proxies a day to anyone
patient. The daily ceiling is what makes a full inventory scrape take
months. A real client never approaches it -- it touches tens of distinct
proxies a day, not hundreds.

Failure modes, stated
---------------------

* These counters live in the `default` cache, like every other throttle
  here. On a per-process backend they are multiplied by the worker count
  (see the CACHES block in `core/settings.py` and
  `core.checks.check_throttle_cache_is_shared`).
* A disclosure is charged **before** the view runs, so a 404 for a proxy
  the user is not entitled to costs a unit too. That is deliberate:
  probing for which ids exist is the first half of a scrape, and it
  should not be free.
* Eviction of the cache key refunds the window. Same property the old
  throttle had.
"""

from rest_framework.throttling import SimpleRateThrottle

__all__ = [
    "CredentialDailyDisclosureThrottle",
    "CredentialDisclosureThrottle",
    "DistinctSubjectRateThrottle",
]


class DistinctSubjectRateThrottle(SimpleRateThrottle):
    """`num_requests` **distinct subjects** per `duration`, per user.

    A `SimpleRateThrottle` whose history records *what* was asked for
    alongside *when*. A request whose subject is already in the window is
    allowed without being recorded again; a request for a subject that is
    not costs one unit.

    Subclasses set `scope`. The subject is the view's lookup kwarg -- for
    `GET /api/proxies/{id}/` that is the proxy id.
    """

    #: Recorded against a request whose subject could not be identified.
    #: Never equal to a real subject, so such a request always costs a
    #: unit rather than accidentally matching a paid-for one.
    UNIDENTIFIED = None

    def get_cache_key(self, request, view):
        """Per user. Anonymous requests are not throttled here -- every view
        carrying this throttle requires authentication, and keying an
        unauthenticated request by IP would put every user behind one NAT
        into a single budget."""
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return self.cache_format % {"scope": self.scope, "ident": user.pk}

    def get_subject(self, request, view):
        """What this request would disclose. `None` means "could not tell",
        which is charged as if it were new."""
        lookup = (
            getattr(view, "lookup_url_kwarg", None)
            or getattr(view, "lookup_field", None)
            or "pk"
        )
        value = (getattr(view, "kwargs", None) or {}).get(lookup)
        if value is None:
            return self.UNIDENTIFIED
        return str(value)[:64]

    def allow_request(self, request, view):
        if self.rate is None:
            return True

        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True

        self.now = self.timer()
        self.history = [
            entry
            for entry in self.cache.get(self.key, [])
            if self._stamp(entry) > self.now - self.duration
        ]

        subject = self.get_subject(request, view)
        if subject is not None and any(
            self._subject(entry) == subject for entry in self.history
        ):
            # Already disclosed inside this window. Free, and deliberately
            # not re-stamped: an active session re-pays once an hour per
            # proxy rather than holding a slot open forever, which keeps
            # the accounting conservative in the direction that matters.
            return True

        if len(self.history) >= self.num_requests:
            return self.throttle_failure()

        # Newest first, matching SimpleRateThrottle so `wait()` can read
        # the oldest entry off the end.
        self.history.insert(0, [self.now, subject])
        self.cache.set(self.key, self.history, self.duration)
        return True

    def wait(self):
        """Seconds until the oldest charged subject falls out of the window.

        Same arithmetic as `SimpleRateThrottle.wait`, reading the
        timestamp out of the `[timestamp, subject]` pair.
        """
        if self.history:
            remaining_duration = self.duration - (self.now - self._stamp(self.history[-1]))
        else:
            remaining_duration = self.duration

        available_requests = self.num_requests - len(self.history) + 1
        if available_requests <= 0:
            return None

        return remaining_duration / float(available_requests)

    @staticmethod
    def _stamp(entry):
        """Timestamp of a history entry.

        Tolerates a bare float so a cache still holding entries written by
        the old request-counting throttle degrades to "charge it" rather
        than raising on the first request after a deploy.
        """
        if isinstance(entry, (list, tuple)):
            return entry[0]
        return entry

    @staticmethod
    def _subject(entry):
        if isinstance(entry, (list, tuple)) and len(entry) > 1:
            return entry[1]
        return None


class CredentialDisclosureThrottle(DistinctSubjectRateThrottle):
    """Bounds how much of the inventory one user can uncover in an hour.

    Keeps the `proxy_credentials` scope and its 60/hour rate, so the
    anti-scrape wall is in the same place it has always been. Only the
    unit moved.
    """

    scope = "proxy_credentials"


class CredentialDailyDisclosureThrottle(DistinctSubjectRateThrottle):
    """Bounds patience rather than bursts.

    An attacker who respects the hourly limit simply waits; a daily
    ceiling is the only thing that makes waiting not work. Mirrors
    `adminapi.throttling.AdminCredentialDailyThrottle`.
    """

    scope = "proxy_credentials_day"
