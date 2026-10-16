"""Quality scoring and the denormalised health fields on `Proxy`.

Everything in `Proxy`'s health block is a **cache of the `ProxyProbe`
table**, recomputed by `recompute_health` from the rows themselves. That
choice is load-bearing in three separate ways and is worth stating before
the code:

1. **Two probe runs cannot corrupt a counter.** There is no read-modify-
   write and nothing is incremented, so there is no lost update to lose and
   no observation to double-count. `recompute_health` is a pure function of
   the probe rows for one proxy; whichever run writes last writes the right
   answer, because by then it can see the other run's rows too.
2. **Re-running is free.** The probe command, the admin trigger and a cron
   timer can all overlap. Running the recompute twice over the same rows
   produces the same values.
3. **The window can change.** Shortening `PROXY_PROBE_WINDOW` is a settings
   change plus `probe_proxies --recompute`, not a migration.

The scoring model
-----------------

`is_alive` was the Phase 1 health model and it cannot express the thing
that actually matters. A proxy that answers every probe but drops 30% of
real connections is *worse* than one that is 80 ms slower, and a boolean
says both are fine. So the ranking signal is a 0-100 `quality_score`:

    score = 100 * reliability * (floor + (1 - floor) * latency_factor)

**Reliability is primary.** It multiplies the whole score, so a proxy that
fails a third of the time cannot climb back by being fast.

**Latency is secondary.** It only ever scales the score within
`[floor, 1.0]` -- by default a 40% band. Latency can break a tie between
two reliable proxies and can demote a catastrophically slow one; it cannot
promote an unreliable one past a reliable one unless the latency gap is
extreme.

**Reliability is a confidence-aware lower bound, not a raw rate.** One
successful probe is not evidence of a 100% success rate, and without this a
freshly-probed proxy with a single lucky success would outrank one with 200
probes at 99%. The Wilson score interval's lower bound handles exactly this
and costs five lines. With the default z of 1.0 a perfect record scores
~0.50 at one probe, ~0.91 at ten and ~0.98 at fifty: the number rises as
the evidence does.

`z = 1.0` rather than the textbook 1.96 is deliberate. At 1.96 a single
successful probe scores 0.21, which would rank it *below* a proxy nothing
has ever probed -- a perverse result, since one success is strictly more
information than none. At z = 1.0 a single success scores almost exactly
the unprobed convention of 0.50, which is the honest statement: one probe
is barely more than no probes.
"""

import math
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional, Sequence

from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from proxies.models import (
    UNPROBED_QUALITY_SCORE,
    ProbeErrorKind,
    Proxy,
    ProxyProbe,
)

__all__ = [
    "HealthSnapshot",
    "compute_quality_score",
    "latency_factor",
    "percentile",
    "recompute_health",
    "record_probe_results",
    "trailing_failures",
    "wilson_lower_bound",
    "window_config",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _setting(name, default):
    return getattr(settings, name, default)


def window_config():
    """`(max_probes, max_age)` for the rolling window.

    Bounded on both axes on purpose. A count alone would judge a proxy
    probed once a month on observations from last quarter; an age alone
    would let a proxy probed every ten seconds swamp the window with a
    single afternoon. The window is "the newest N probes, none older than
    D days".
    """
    return (
        int(_setting("PROXY_PROBE_WINDOW", 50)),
        timedelta(days=int(_setting("PROXY_PROBE_WINDOW_DAYS", 7))),
    )


def dead_after_failures():
    """Consecutive failures that mark a proxy dead.

    Must be > 1. A threshold of 1 is the Phase 1 behaviour this feature
    exists to replace: one timeout on a loaded network is noise, and
    killing a good proxy for it is how a health check becomes something
    operators turn off.
    """
    return max(2, int(_setting("PROXY_DEAD_AFTER_FAILURES", 3)))


def unprobed_score():
    return float(_setting("PROXY_UNPROBED_SCORE", UNPROBED_QUALITY_SCORE))


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def percentile(values: Sequence[int], fraction: float) -> Optional[int]:
    """Nearest-rank percentile. Returns an observed value, never an
    interpolated one.

    Nearest rank rather than linear interpolation because a latency figure
    the operator can find in the probe table is worth more than one that is
    marginally more "correct" and corresponds to nothing that happened.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return int(ordered[min(rank, len(ordered)) - 1])


def wilson_lower_bound(successes: int, trials: int, z: Optional[float] = None) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion.

    The point of using it instead of `successes / trials` is small samples:
    1/1 is not the same claim as 200/200, and a ranking that treats them
    alike hands every slot to whichever proxy was probed least.
    """
    if trials <= 0:
        return 0.0
    if z is None:
        z = float(_setting("PROXY_SCORE_CONFIDENCE_Z", 1.0))
    p = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    centre = p + z2 / (2 * trials)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4 * trials)) / trials)
    return max(0.0, min(1.0, (centre - margin) / denominator))


def latency_factor(p95_ms: Optional[int]) -> float:
    """Map a p95 latency to a 0-1 desirability factor.

    `half / (half + p95)`: 1.0 at zero latency, exactly 0.5 at the
    configured half-point, and asymptotic to 0. A smooth curve rather than
    a threshold, so a proxy does not jump ranks because it crossed 200 ms
    by a millisecond.
    """
    if p95_ms is None:
        return 1.0
    half = max(1.0, float(_setting("PROXY_LATENCY_HALF_MS", 400)))
    return half / (half + max(0.0, float(p95_ms)))


def compute_quality_score(
    success_rate: Optional[float],
    probe_count: int,
    latency_p95_ms: Optional[int],
) -> float:
    """The 0-100 ranking signal. Pure function -- see the module docstring.

    An unprobed proxy returns the unprobed convention rather than 0: "we
    have not measured this" must rank below anything proven good and above
    anything proven bad, and zero would make a fresh CSV import
    unselectable.
    """
    if not probe_count or success_rate is None:
        return unprobed_score()

    successes = int(round(success_rate * probe_count))
    reliability = wilson_lower_bound(successes, probe_count)

    weight = float(_setting("PROXY_LATENCY_WEIGHT", 0.4))
    weight = min(max(weight, 0.0), 1.0)
    floor = 1.0 - weight

    return round(100.0 * reliability * (floor + weight * latency_factor(latency_p95_ms)), 4)


def trailing_failures(succeeded_newest_first: Iterable[bool]) -> int:
    """Failures at the head of the history, stopping at the first success.

    This, not the single most recent probe, is what `is_alive` is derived
    from. One failure is noise; `PROXY_DEAD_AFTER_FAILURES` in a row is a
    pattern.
    """
    count = 0
    for succeeded in succeeded_newest_first:
        if succeeded:
            break
        count += 1
    return count


# ---------------------------------------------------------------------------
# Recomputation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthSnapshot:
    """The computed health block, before it is written."""

    is_alive: bool
    success_rate: Optional[float]
    probe_count: int
    consecutive_failures: int
    latency_ms: Optional[int]
    latency_p50_ms: Optional[int]
    latency_p95_ms: Optional[int]
    quality_score: float
    last_checked: Optional[object]

    def as_update(self):
        return {
            "is_alive": self.is_alive,
            "success_rate": self.success_rate,
            "probe_count": self.probe_count,
            "consecutive_failures": self.consecutive_failures,
            "latency_ms": self.latency_ms,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "quality_score": self.quality_score,
            "last_checked": self.last_checked,
        }


def snapshot_from_probes(rows, *, now=None) -> HealthSnapshot:
    """Build a `HealthSnapshot` from probe rows, newest first.

    `rows` is an iterable of `(succeeded, latency_ms, checked_at)` ordered
    newest-first and **not** yet age-filtered: the age cutoff is applied
    here so that `last_checked` can still report a probe older than the
    window. An operator needs to see "last seen 40 days ago"; replacing it
    with null would read as "never probed".
    """
    now = now or timezone.now()
    window_size, max_age = window_config()
    cutoff = now - max_age

    rows = list(rows)
    last_checked = rows[0][2] if rows else None

    window = [row for row in rows if row[2] >= cutoff][:window_size]

    if not window:
        # Every observation has aged out. Deliberately reverts to the
        # unprobed state rather than preserving a stale verdict: health
        # data has a shelf life, and "alive, 12 ms, measured in March" is a
        # worse answer than "unknown".
        return HealthSnapshot(
            is_alive=True,
            success_rate=None,
            probe_count=0,
            consecutive_failures=0,
            latency_ms=None,
            latency_p50_ms=None,
            latency_p95_ms=None,
            quality_score=unprobed_score(),
            last_checked=last_checked,
        )

    successes = [row for row in window if row[0]]
    latencies = [row[1] for row in successes if row[1] is not None]

    probe_count = len(window)
    success_rate = len(successes) / probe_count
    failures = trailing_failures(row[0] for row in window)
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)

    # Last successful latency, for the Phase 1 `latency_ms` contract.
    last_success_latency = next(
        (row[1] for row in window if row[0] and row[1] is not None), None
    )

    return HealthSnapshot(
        is_alive=failures < dead_after_failures(),
        success_rate=success_rate,
        probe_count=probe_count,
        consecutive_failures=failures,
        latency_ms=last_success_latency,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        quality_score=compute_quality_score(success_rate, probe_count, p95),
        last_checked=last_checked,
    )


def _lock(queryset):
    """`SELECT ... FOR UPDATE` where the backend has it.

    SQLite does not, and raises rather than quietly ignoring it, so the
    feature flag is checked instead of assumed. SQLite serialises writing
    transactions at the file level anyway, which gives the same guarantee
    by a blunter route -- see `_retry_if_locked` for the cost of that.
    """
    if connection.features.has_select_for_update:
        return queryset.select_for_update()
    return queryset


# Two probe runs overlapping is a normal, supported situation: a cron tick
# that overruns its interval, or an operator pressing "probe these now"
# while the scheduled run is working. On PostgreSQL the row lock in
# `recompute_health` handles it and a second writer simply waits.
#
# SQLite does not wait. A concurrent writer gets `database is locked` --
# and under the shared cache Django uses for threaded tests, the harder
# `database table is locked`, which `OPTIONS["timeout"]` does not cover
# because it is a cache-level lock rather than a file-level one. Neither
# is corruption; the write is refused outright. But a probe run that dies
# two thirds of the way through because another one was running is a
# scheduled job that fails intermittently for no reason an operator can
# act on.
#
# So locked writes are retried with a short backoff. This is safe
# precisely because of the design above: `recompute_health` is idempotent,
# so a retry recomputes from the rows and lands on the same answer, and
# the `bulk_create` is a single statement that wrote nothing if it failed.
_LOCK_RETRIES = 5
_LOCK_BACKOFF_SECONDS = 0.05


def _retry_if_locked(operation, retries=_LOCK_RETRIES):
    for attempt in range(retries):
        try:
            return operation()
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == retries - 1:
                raise
            # Jittered by attempt so two contending writers do not retry
            # in lockstep forever.
            time.sleep(_LOCK_BACKOFF_SECONDS * (attempt + 1))
    raise AssertionError("unreachable")


def recompute_health(proxy_id: int, *, now=None) -> Optional[HealthSnapshot]:
    """Rebuild one proxy's health block from its probe rows.

    Idempotent and concurrency-safe. Returns None if the proxy is gone --
    a probe run racing a delete is an ordinary event, not an error.
    """
    return _retry_if_locked(lambda: _recompute_health(proxy_id, now=now))


def _recompute_health(proxy_id, *, now=None):
    now = now or timezone.now()
    window_size, _ = window_config()

    with transaction.atomic():
        locked = _lock(Proxy.objects.filter(pk=proxy_id)).values_list("pk", flat=True)
        if not list(locked[:1]):
            return None

        # Fetch without the age cutoff so `last_checked` survives a probe
        # that has aged out of the window; `snapshot_from_probes` applies
        # the cutoff. Capped at the window size, so this stays O(window)
        # however many rows the proxy has accumulated.
        rows = list(
            ProxyProbe.objects.filter(proxy_id=proxy_id)
            .order_by("-checked_at", "-pk")
            .values_list("succeeded", "latency_ms", "checked_at")[:window_size]
        )

        snapshot = snapshot_from_probes(rows, now=now)

        # `.update()` rather than `instance.save()`: it touches only the
        # health columns, so the EncryptedCharField credential columns are
        # never rewritten and `updated_at` does not move. A health
        # recompute is not a change to the proxy.
        Proxy.objects.filter(pk=proxy_id).update(**snapshot.as_update())

    return snapshot


def record_probe_results(results, *, now=None, recompute=True):
    """Persist probe observations and refresh the proxies they touched.

    `results` is an iterable of `(proxy_id, ProbeResult, target)`.

    The probe rows are written in one `bulk_create`; the recompute then
    runs per proxy in its own short transaction. Deliberately not one big
    transaction: a probe run over 10k proxies holding a single write
    transaction would block every other writer for its whole duration, and
    the rows are independent anyway.
    """
    now = now or timezone.now()
    results = list(results)
    if not results:
        return []

    probes = [
        ProxyProbe(
            proxy_id=proxy_id,
            checked_at=getattr(result, "checked_at", None) or now,
            succeeded=result.succeeded,
            latency_ms=result.latency_ms if result.succeeded else None,
            error_kind="" if result.succeeded else (result.error_kind or ProbeErrorKind.UNKNOWN),
            target=(target or "")[:120],
        )
        for proxy_id, result, target in results
    ]
    _retry_if_locked(lambda: ProxyProbe.objects.bulk_create(probes, batch_size=500))

    if not recompute:
        return probes

    for proxy_id in dict.fromkeys(proxy_id for proxy_id, _result, _target in results):
        recompute_health(proxy_id, now=now)

    return probes


def recompute_all(queryset=None, *, now=None, batch_size=500):
    """Recompute every proxy's health from existing probe rows.

    Used by `probe_proxies --recompute` after a window or weighting
    change: the stored `quality_score` is a cache, and a settings change
    does not retroactively rewrite it.
    """
    queryset = Proxy.objects.all() if queryset is None else queryset
    count = 0
    for proxy_id in queryset.values_list("pk", flat=True).iterator(
        chunk_size=batch_size
    ):
        if recompute_health(proxy_id, now=now) is not None:
            count += 1
    return count
