"""Health scoring, probe recording, and the two management commands.

**Every test here uses `FakeProber`.** Nothing in this file opens a socket.
A test that needs a live proxy is a test that fails in CI, fails offline,
and fails in the sandbox this project is developed in -- and it would be
testing somebody else's network rather than our code.
"""

import threading
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from proxies.checks import (
    check_health_thresholds,
    check_probe_retention_covers_window,
)
from proxies.health import (
    compute_quality_score,
    latency_factor,
    percentile,
    recompute_all,
    recompute_health,
    record_probe_results,
    snapshot_from_probes,
    trailing_failures,
    wilson_lower_bound,
)
from proxies.models import (
    ProbeErrorKind,
    Proxy,
    ProxyProbe,
    ProxyProtocol,
    ProxyTier,
    UNPROBED_QUALITY_SCORE,
)
from proxies.probing import (
    BatchOutcome,
    FakeProber,
    ProbeResult,
    ProbeUnsupported,
    ProxyTarget,
    SocketProber,
    get_prober,
    probe_batch,
)


def make_proxy(**kwargs):
    defaults = {
        "protocol": ProxyProtocol.SOCKS5,
        "host": f"198.51.100.{Proxy.objects.count() + 1}",
        "port": 1080,
        "tier": ProxyTier.PREMIUM,
        "country": "NL",
    }
    defaults.update(kwargs)
    return Proxy.objects.create(**defaults)


def write_probes(proxy, outcomes, *, end=None, spacing=timedelta(minutes=1)):
    """Write probe rows for `proxy`.

    `outcomes` is **oldest first** -- the order you would read a history
    in -- as a list of `True`/`False` or `(succeeded, latency_ms)`. The
    last entry ends up newest, which is the end the rolling window and the
    consecutive-failure count both work from.
    """
    end = end or timezone.now()
    rows = []
    count = len(outcomes)
    for index, outcome in enumerate(outcomes):
        succeeded, latency = outcome if isinstance(outcome, tuple) else (outcome, 100)
        rows.append(
            ProxyProbe(
                proxy=proxy,
                checked_at=end - spacing * (count - 1 - index),
                succeeded=succeeded,
                latency_ms=latency if succeeded else None,
                error_kind="" if succeeded else ProbeErrorKind.TIMEOUT,
                target="example.test:443",
            )
        )
    ProxyProbe.objects.bulk_create(rows)
    return rows


# ---------------------------------------------------------------------------
# The statistics, as pure functions
# ---------------------------------------------------------------------------


class PercentileTests(TestCase):
    def test_nearest_rank_returns_an_observed_value(self):
        values = list(range(10, 210, 10))  # 10..200, n=20
        self.assertEqual(percentile(values, 0.50), 100)
        self.assertEqual(percentile(values, 0.95), 190)
        self.assertIn(percentile(values, 0.95), values)

    def test_p95_is_not_the_mean_and_that_is_the_point(self):
        """A good mean hiding a terrible tail is the case this exists for."""
        values = [10] * 19 + [5000]
        self.assertEqual(percentile(values, 0.50), 10)
        self.assertEqual(percentile(values, 0.95), 10)
        # n=20: the 5000 is the 20th value, p95 is the 19th. Add one more
        # slow sample and the tail becomes visible, which is correct --
        # one outlier in twenty is not a p95.
        values = [10] * 18 + [5000, 5000]
        self.assertEqual(percentile(values, 0.95), 5000)
        self.assertEqual(sum(values) / len(values), 509.0)  # mean says "fine"

    def test_single_sample(self):
        self.assertEqual(percentile([42], 0.95), 42)
        self.assertEqual(percentile([42], 0.50), 42)

    def test_empty_is_none_not_zero(self):
        # Zero would read as "instant", which is the opposite of "unknown".
        self.assertIsNone(percentile([], 0.95))

    def test_unsorted_input(self):
        self.assertEqual(percentile([200, 10, 50, 30], 0.50), 30)


class WilsonTests(TestCase):
    def test_confidence_rises_with_the_sample(self):
        """A perfect record is worth more the longer it is."""
        one = wilson_lower_bound(1, 1)
        ten = wilson_lower_bound(10, 10)
        fifty = wilson_lower_bound(50, 50)
        self.assertLess(one, ten)
        self.assertLess(ten, fifty)
        self.assertAlmostEqual(one, 0.50, places=2)
        self.assertAlmostEqual(ten, 0.91, places=2)
        self.assertAlmostEqual(fifty, 0.98, places=2)

    def test_a_single_lucky_success_does_not_outrank_a_long_record(self):
        """The failure this function exists to prevent."""
        lucky = wilson_lower_bound(1, 1)  # 100% over one probe
        proven = wilson_lower_bound(198, 200)  # 99% over two hundred
        self.assertLess(lucky, proven)

    def test_bounds(self):
        self.assertEqual(wilson_lower_bound(0, 0), 0.0)
        self.assertEqual(wilson_lower_bound(0, 10), 0.0)
        self.assertTrue(0.0 <= wilson_lower_bound(7, 10) <= 1.0)


class LatencyFactorTests(TestCase):
    @override_settings(PROXY_LATENCY_HALF_MS=400)
    def test_half_point_is_exactly_half(self):
        self.assertAlmostEqual(latency_factor(400), 0.5)
        self.assertAlmostEqual(latency_factor(0), 1.0)
        self.assertLess(latency_factor(2000), 0.2)

    def test_unknown_latency_is_neutral(self):
        self.assertEqual(latency_factor(None), 1.0)


class QualityScoreTests(TestCase):
    """Reliability primary, latency secondary -- the roadmap's actual ask."""

    def test_the_roadmaps_example_comes_out_right(self):
        """'A proxy that drops 30% of connections is worse than one 80 ms
        slower' -- roadmap.md item 1."""
        lossy_and_fast = compute_quality_score(0.70, 50, 100)
        reliable_and_slower = compute_quality_score(1.00, 50, 180)
        self.assertGreater(reliable_and_slower, lossy_and_fast)

    def test_latency_cannot_rescue_an_unreliable_proxy(self):
        """Instant but lossy still loses to reliable but slow."""
        instant_but_lossy = compute_quality_score(0.60, 50, 1)
        slow_but_perfect = compute_quality_score(1.00, 50, 500)
        self.assertGreater(slow_but_perfect, instant_but_lossy)

    def test_latency_breaks_ties_between_equally_reliable_proxies(self):
        fast = compute_quality_score(1.0, 50, 50)
        slow = compute_quality_score(1.0, 50, 800)
        self.assertGreater(fast, slow)

    @override_settings(PROXY_LATENCY_WEIGHT=0.4)
    def test_latency_influence_is_bounded_by_the_weight(self):
        """Latency may move a score within the configured band and no
        further. This is what makes it *secondary* rather than merely
        weighted."""
        best = compute_quality_score(1.0, 50, 0)
        worst = compute_quality_score(1.0, 50, 10**9)
        self.assertAlmostEqual(worst / best, 0.6, places=2)

    def test_unprobed_is_the_convention_not_zero(self):
        """Zero would make a fresh CSV import unselectable."""
        self.assertEqual(compute_quality_score(None, 0, None), UNPROBED_QUALITY_SCORE)
        self.assertEqual(compute_quality_score(1.0, 0, 10), UNPROBED_QUALITY_SCORE)

    def test_unprobed_ranks_between_proven_good_and_proven_bad(self):
        unprobed = compute_quality_score(None, 0, None)
        proven_good = compute_quality_score(1.0, 50, 100)
        proven_bad = compute_quality_score(0.2, 50, 100)
        self.assertLess(proven_bad, unprobed)
        self.assertLess(unprobed, proven_good)

    def test_total_failure_scores_zero(self):
        self.assertEqual(compute_quality_score(0.0, 50, None), 0.0)


class TrailingFailureTests(TestCase):
    def test_counts_only_the_head(self):
        self.assertEqual(trailing_failures([False, False, True, False]), 2)

    def test_a_success_at_the_head_resets(self):
        self.assertEqual(trailing_failures([True, False, False, False]), 0)

    def test_all_failures(self):
        self.assertEqual(trailing_failures([False] * 5), 5)


# ---------------------------------------------------------------------------
# The rolling window
# ---------------------------------------------------------------------------


@override_settings(PROXY_PROBE_WINDOW=10, PROXY_PROBE_WINDOW_DAYS=7)
class RollingWindowTests(TestCase):
    def setUp(self):
        self.proxy = make_proxy()

    def test_success_rate_is_the_window_not_the_lifetime(self):
        """Twenty old failures then ten recent successes is a healthy
        proxy, and a lifetime rate would call it 33%."""
        write_probes(
            self.proxy,
            [False] * 20 + [True] * 10,
            end=timezone.now(),
            spacing=timedelta(minutes=1),
        )
        snapshot = recompute_health(self.proxy.pk)
        self.assertEqual(snapshot.probe_count, 10)
        self.assertEqual(snapshot.success_rate, 1.0)

    def test_success_rate_maths(self):
        write_probes(self.proxy, [True] * 7 + [False] * 3)
        snapshot = recompute_health(self.proxy.pk)
        self.assertEqual(snapshot.probe_count, 10)
        self.assertAlmostEqual(snapshot.success_rate, 0.7)

    def test_probes_older_than_the_window_age_out(self):
        write_probes(
            self.proxy, [True] * 5, end=timezone.now() - timedelta(days=30)
        )
        snapshot = recompute_health(self.proxy.pk)
        self.assertEqual(snapshot.probe_count, 0)
        self.assertIsNone(snapshot.success_rate)
        self.assertEqual(snapshot.quality_score, UNPROBED_QUALITY_SCORE)

    def test_last_checked_survives_the_window_expiring(self):
        """'Last seen 30 days ago' is information; null reads as 'never'."""
        write_probes(
            self.proxy, [True], end=timezone.now() - timedelta(days=30)
        )
        snapshot = recompute_health(self.proxy.pk)
        self.assertEqual(snapshot.probe_count, 0)
        self.assertIsNotNone(snapshot.last_checked)

    def test_percentiles_use_only_successful_probes(self):
        """A failure has no latency; borrowing the timeout would make p95 a
        measure of the timeout setting."""
        write_probes(self.proxy, [(True, 10), (False, None), (True, 30)])
        snapshot = recompute_health(self.proxy.pk)
        self.assertEqual(snapshot.latency_p50_ms, 10)
        self.assertEqual(snapshot.latency_p95_ms, 30)

    def test_p95_is_computed_and_stored(self):
        latencies = [(True, value) for value in range(10, 210, 10)]
        with override_settings(PROXY_PROBE_WINDOW=20):
            write_probes(self.proxy, latencies)
            recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.latency_p50_ms, 100)
        self.assertEqual(self.proxy.latency_p95_ms, 190)

    def test_latency_ms_is_the_last_successful_probe(self):
        write_probes(self.proxy, [(True, 500), (True, 25), (False, None)])
        self.proxy.refresh_from_db()
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.latency_ms, 25)

    def test_recompute_does_not_touch_updated_at_or_credentials(self):
        """A health recompute is not a change to the proxy."""
        proxy = make_proxy(username="paid-user", password="paid-pass")
        before = Proxy.objects.get(pk=proxy.pk).updated_at
        write_probes(proxy, [True] * 3)
        recompute_health(proxy.pk)
        after = Proxy.objects.get(pk=proxy.pk)
        self.assertEqual(after.updated_at, before)
        self.assertEqual(after.username, "paid-user")
        self.assertEqual(after.password, "paid-pass")


# ---------------------------------------------------------------------------
# Flapping suppression -- the behaviour `is_alive` could not express
# ---------------------------------------------------------------------------


@override_settings(PROXY_DEAD_AFTER_FAILURES=3, PROXY_PROBE_WINDOW=50)
class FlappingSuppressionTests(TestCase):
    def setUp(self):
        self.proxy = make_proxy()

    def test_a_single_failure_does_not_kill_a_proxy(self):
        """One timeout on a loaded network is noise."""
        write_probes(self.proxy, [True, True, True, False])
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertTrue(self.proxy.is_alive)
        self.assertEqual(self.proxy.consecutive_failures, 1)

    def test_two_failures_still_do_not(self):
        write_probes(self.proxy, [True, True, False, False])
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertTrue(self.proxy.is_alive)
        self.assertEqual(self.proxy.consecutive_failures, 2)

    def test_the_threshold_number_of_consecutive_failures_kills_it(self):
        write_probes(self.proxy, [True, False, False, False])
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertFalse(self.proxy.is_alive)
        self.assertEqual(self.proxy.consecutive_failures, 3)

    def test_failures_must_be_consecutive_not_merely_numerous(self):
        """Five failures interleaved with successes is a degraded proxy,
        not a dead one -- and the success rate is what says so."""
        write_probes(
            self.proxy, [False, True, False, True, False, True, False, True, False, True]
        )
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertTrue(self.proxy.is_alive)
        self.assertEqual(self.proxy.consecutive_failures, 0)
        self.assertAlmostEqual(self.proxy.success_rate, 0.5)
        # Alive, but the score reflects what it really is.
        self.assertLess(self.proxy.quality_score, UNPROBED_QUALITY_SCORE)

    def test_one_success_revives_a_dead_proxy(self):
        """Deliberately asymmetric: slow to die, quick to recover. A proxy
        that flaps back and forth is punished by its success rate, which
        is a better signal than the boolean."""
        write_probes(self.proxy, [False, False, False])
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertFalse(self.proxy.is_alive)

        write_probes(self.proxy, [True], end=timezone.now() + timedelta(minutes=1))
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertTrue(self.proxy.is_alive)
        self.assertEqual(self.proxy.consecutive_failures, 0)

    @override_settings(PROXY_DEAD_AFTER_FAILURES=5)
    def test_the_threshold_is_configurable(self):
        write_probes(self.proxy, [False] * 4)
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertTrue(self.proxy.is_alive)

        write_probes(self.proxy, [False], end=timezone.now() + timedelta(minutes=1))
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertFalse(self.proxy.is_alive)

    @override_settings(PROXY_DEAD_AFTER_FAILURES=1)
    def test_a_threshold_of_one_is_clamped_to_two(self):
        """At 1 this is the Phase 1 behaviour the feature replaces, so the
        value is floored rather than honoured. `proxies.W002` warns."""
        write_probes(self.proxy, [True, False])
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertTrue(self.proxy.is_alive)


# ---------------------------------------------------------------------------
# Recording results -- idempotence and concurrency
# ---------------------------------------------------------------------------


@override_settings(PROXY_PROBE_WINDOW=50, PROXY_DEAD_AFTER_FAILURES=3)
class RecordProbeResultsTests(TestCase):
    def setUp(self):
        self.proxy = make_proxy()

    def test_writes_rows_and_refreshes_health(self):
        record_probe_results(
            [(self.proxy.pk, ProbeResult.ok(42), "example.test:443")]
        )
        self.proxy.refresh_from_db()
        self.assertEqual(ProxyProbe.objects.count(), 1)
        self.assertEqual(self.proxy.probe_count, 1)
        self.assertEqual(self.proxy.latency_ms, 42)
        self.assertEqual(self.proxy.success_rate, 1.0)

    def test_a_failure_stores_no_latency(self):
        record_probe_results(
            [
                (
                    self.proxy.pk,
                    ProbeResult.failed(ProbeErrorKind.TIMEOUT, "timed out"),
                    "example.test:443",
                )
            ]
        )
        probe = ProxyProbe.objects.get()
        self.assertFalse(probe.succeeded)
        self.assertIsNone(probe.latency_ms)
        self.assertEqual(probe.error_kind, ProbeErrorKind.TIMEOUT)

    def test_an_error_kind_is_always_recorded_for_a_failure(self):
        record_probe_results(
            [(self.proxy.pk, ProbeResult(succeeded=False), "t:443")]
        )
        self.assertEqual(ProxyProbe.objects.get().error_kind, ProbeErrorKind.UNKNOWN)

    def test_recomputation_is_idempotent(self):
        """Running the health update twice over the same rows must not
        double-count anything -- the property that makes overlapping probe
        runs safe."""
        write_probes(self.proxy, [True, True, False])
        first = recompute_health(self.proxy.pk)
        second = recompute_health(self.proxy.pk)
        self.assertEqual(first.as_update(), second.as_update())
        self.assertEqual(first.probe_count, 3)

    def test_a_stale_in_memory_proxy_cannot_lose_another_runs_update(self):
        """The classic lost update, deterministically.

        Run A loads the proxy. Run B then probes and updates it. Run A --
        still holding its stale object -- writes its own probe and updates.
        Because the health fields are *recomputed from the probe table*
        rather than incremented from the object in hand, run A's write
        includes run B's observations instead of clobbering them.
        """
        stale = Proxy.objects.get(pk=self.proxy.pk)
        self.assertEqual(stale.probe_count, 0)

        record_probe_results([(self.proxy.pk, ProbeResult.ok(10), "t:443")])
        record_probe_results([(self.proxy.pk, ProbeResult.ok(20), "t:443")])

        # Run A acts on its stale snapshot.
        record_probe_results([(stale.pk, ProbeResult.ok(30), "t:443")])

        self.proxy.refresh_from_db()
        self.assertEqual(ProxyProbe.objects.count(), 3)
        self.assertEqual(self.proxy.probe_count, 3)
        self.assertEqual(self.proxy.success_rate, 1.0)

    def test_interleaved_failure_and_success_runs_agree_with_the_rows(self):
        """Whatever order two runs land in, the stored counters are a pure
        function of the probe table."""
        for result in (
            ProbeResult.failed(ProbeErrorKind.TIMEOUT),
            ProbeResult.failed(ProbeErrorKind.REFUSED),
            ProbeResult.failed(ProbeErrorKind.TIMEOUT),
        ):
            record_probe_results([(self.proxy.pk, result, "t:443")])

        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.consecutive_failures, 3)
        self.assertFalse(self.proxy.is_alive)

        rows = list(
            ProxyProbe.objects.filter(proxy=self.proxy)
            .order_by("-checked_at", "-pk")
            .values_list("succeeded", "latency_ms", "checked_at")
        )
        expected = snapshot_from_probes(rows)
        self.assertEqual(expected.consecutive_failures, 3)
        self.assertEqual(expected.is_alive, self.proxy.is_alive)

    def test_empty_input_is_a_no_op(self):
        self.assertEqual(record_probe_results([]), [])
        self.assertEqual(ProxyProbe.objects.count(), 0)

    def test_a_deleted_proxy_does_not_raise(self):
        pk = self.proxy.pk
        self.proxy.delete()
        self.assertIsNone(recompute_health(pk))

    def test_recompute_all_rescored_everything(self):
        other = make_proxy(host="203.0.113.77")
        write_probes(self.proxy, [True] * 5)
        write_probes(other, [False] * 5)
        self.assertEqual(recompute_all(), 2)
        self.proxy.refresh_from_db()
        other.refresh_from_db()
        self.assertGreater(self.proxy.quality_score, other.quality_score)


class ConcurrentProbeRunTests(TransactionTestCase):
    """Two probe runs, genuinely in parallel, on the same proxies.

    `TransactionTestCase` because the threads need to see each other's
    committed writes, which a `TestCase`'s wrapping transaction prevents.
    """

    reset_sequences = True

    @override_settings(PROXY_PROBE_WINDOW=100, PROXY_DEAD_AFTER_FAILURES=3)
    def test_overlapping_runs_do_not_corrupt_the_counters(self):
        proxies = [make_proxy(host=f"203.0.113.{index}") for index in range(3)]
        per_run = 5
        barrier = threading.Barrier(2)
        errors = []

        def run(latency):
            try:
                barrier.wait(timeout=30)
                for _ in range(per_run):
                    record_probe_results(
                        [
                            (proxy.pk, ProbeResult.ok(latency), "t:443")
                            for proxy in proxies
                        ]
                    )
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                # Each thread opened its own connection; leaving it open
                # keeps a SQLite file lock that the teardown truncation
                # would then block on.
                connection.close()

        threads = [
            threading.Thread(target=run, args=(10,)),
            threading.Thread(target=run, args=(20,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [], f"probe threads raised: {errors}")

        for proxy in proxies:
            proxy.refresh_from_db()
            rows = ProxyProbe.objects.filter(proxy=proxy).count()
            self.assertEqual(
                rows, per_run * 2, "every observation must be recorded exactly once"
            )
            self.assertEqual(
                proxy.probe_count,
                rows,
                "the denormalised count must match the probe table",
            )
            self.assertEqual(proxy.success_rate, 1.0)
            self.assertEqual(proxy.consecutive_failures, 0)
            self.assertTrue(proxy.is_alive)


# ---------------------------------------------------------------------------
# The prober abstraction
# ---------------------------------------------------------------------------


class FakeProberTests(TestCase):
    def setUp(self):
        self.target = ProxyTarget(
            id=1, protocol=ProxyProtocol.SOCKS5, host="h", port=1080
        )

    def test_scripted_sequence_is_consumed_in_order_and_the_last_repeats(self):
        prober = FakeProber(
            {
                1: [
                    ProbeResult.failed(ProbeErrorKind.TIMEOUT),
                    ProbeResult.failed(ProbeErrorKind.REFUSED),
                    ProbeResult.ok(15),
                ]
            }
        )
        kinds = [
            prober.probe(self.target, destination=("t", 443), timeout=1)
            for _ in range(5)
        ]
        self.assertEqual(
            [result.error_kind for result in kinds[:2]],
            [ProbeErrorKind.TIMEOUT, ProbeErrorKind.REFUSED],
        )
        self.assertTrue(all(result.succeeded for result in kinds[2:]))

    def test_records_every_call(self):
        prober = FakeProber()
        prober.probe(self.target, destination=("t", 443), timeout=1)
        prober.probe(self.target, destination=("t", 443), timeout=1)
        self.assertEqual(prober.calls, [1, 1])

    def test_can_be_scripted_to_be_unsupported(self):
        prober = FakeProber(unsupported={ProxyProtocol.SHADOWSOCKS.value})
        ss = ProxyTarget(id=2, protocol=ProxyProtocol.SHADOWSOCKS, host="h", port=8388)
        with self.assertRaises(ProbeUnsupported):
            prober.probe(ss, destination=("t", 443), timeout=1)


class ProbeBatchTests(TestCase):
    def targets(self, count):
        return [
            ProxyTarget(id=index, protocol=ProxyProtocol.SOCKS5, host="h", port=1080)
            for index in range(1, count + 1)
        ]

    def test_results_are_collected_for_every_target(self):
        outcome = probe_batch(FakeProber(), self.targets(5), concurrency=3)
        self.assertEqual(len(outcome.results), 5)
        self.assertEqual(outcome.succeeded, 5)
        self.assertEqual(outcome.failed, 0)

    def test_unsupported_protocols_are_skipped_not_failed(self):
        """The distinction that keeps a working Shadowsocks proxy out of
        the graveyard."""
        prober = FakeProber(unsupported={2})
        outcome = probe_batch(prober, self.targets(3))
        self.assertEqual(len(outcome.results), 2)
        self.assertEqual([pk for pk, _reason in outcome.skipped], [2])
        self.assertEqual(outcome.failed, 0)

    def test_a_prober_bug_is_not_recorded_as_the_proxy_failing(self):
        """Our exception must not mark the inventory dead."""

        class Exploding(FakeProber):
            def probe(self, target, **kwargs):
                raise RuntimeError("boom")

        with self.assertLogs("proxycrack.probing", level="ERROR"):
            outcome = probe_batch(Exploding(), self.targets(2))
        self.assertEqual(outcome.results, [])
        self.assertEqual(len(outcome.errored), 2)

    def test_empty_input(self):
        self.assertEqual(probe_batch(FakeProber(), []).results, [])

    def test_the_destination_is_recorded_on_every_row(self):
        outcome = probe_batch(
            FakeProber(), self.targets(1), destination=("probe.test", 8443)
        )
        self.assertEqual(outcome.results[0][2], "probe.test:8443")


class SocketProberDispatchTests(TestCase):
    """Dispatch only -- no sockets. The handshakes themselves are not
    exercised here because doing so honestly needs a live proxy."""

    def test_shadowsocks_with_an_unsupported_cipher_is_refused_before_a_socket(self):
        """Was "Shadowsocks is always refused" (P14). SS is probed now;
        what is still refused is a cipher this client does not implement,
        and it is still refused before any socket is opened. The full
        Shadowsocks suite is in `proxies/test_shadowsocks.py`."""
        prober = SocketProber()
        with self.assertRaises(ProbeUnsupported) as caught:
            prober.handler_for(
                ProxyProtocol.SHADOWSOCKS.value,
                ProxyTarget(
                    id=1,
                    protocol=ProxyProtocol.SHADOWSOCKS.value,
                    host="198.51.100.22",
                    port=8388,
                    ss_method="2022-blake3-aes-256-gcm",
                ),
            )
        self.assertEqual(caught.exception.code, ProbeUnsupported.UNSUPPORTED_CIPHER)

    def test_all_three_committed_protocols_resolve(self):
        prober = SocketProber()
        self.assertTrue(callable(prober.handler_for(ProxyProtocol.SOCKS5.value)))
        self.assertTrue(callable(prober.handler_for(ProxyProtocol.HTTP.value)))
        self.assertTrue(
            callable(prober.handler_for(ProxyProtocol.SHADOWSOCKS.value))
        )

    def test_an_unknown_protocol_is_unsupported_rather_than_a_crash(self):
        with self.assertRaises(ProbeUnsupported):
            SocketProber().handler_for("carrier-pigeon")

    @override_settings(PROXY_PROBER="proxies.probing.FakeProber")
    def test_get_prober_honours_the_setting(self):
        self.assertIsInstance(get_prober(), FakeProber)

    def test_the_default_prober_is_the_real_one(self):
        self.assertIsInstance(get_prober(), SocketProber)


# ---------------------------------------------------------------------------
# Management commands
# ---------------------------------------------------------------------------


@override_settings(
    PROXY_PROBER="proxies.probing.FakeProber",
    PROXY_PROBE_WINDOW=50,
    PROXY_DEAD_AFTER_FAILURES=3,
)
class ProbeProxiesCommandTests(TestCase):
    def setUp(self):
        self.nl = make_proxy(host="203.0.113.1", country="NL", tier=ProxyTier.PREMIUM)
        self.de = make_proxy(host="203.0.113.2", country="DE", tier=ProxyTier.FREE)
        self.disabled = make_proxy(host="203.0.113.3", is_enabled=False)

    def call(self, *args, prober=None, **options):
        out = StringIO()
        options.setdefault("stdout", out)
        options.setdefault("stderr", StringIO())
        if prober is None:
            call_command("probe_proxies", *args, **options)
        else:
            with mock.patch(
                "proxies.management.commands.probe_proxies.get_prober",
                return_value=prober,
            ):
                call_command("probe_proxies", *args, **options)
        return out.getvalue()

    def test_probes_every_enabled_proxy_and_writes_rows(self):
        output = self.call()
        self.assertEqual(ProxyProbe.objects.count(), 2)
        self.assertIn("2 probed", output)
        self.nl.refresh_from_db()
        self.assertEqual(self.nl.probe_count, 1)
        self.assertIsNotNone(self.nl.last_checked)

    def test_disabled_proxies_are_skipped_by_default(self):
        self.call()
        self.assertFalse(ProxyProbe.objects.filter(proxy=self.disabled).exists())

    def test_include_disabled_probes_them(self):
        self.call("--include-disabled")
        self.assertTrue(ProxyProbe.objects.filter(proxy=self.disabled).exists())

    def test_tier_selection(self):
        prober = FakeProber()
        self.call("--tier", "free", prober=prober)
        self.assertEqual(prober.calls, [self.de.pk])

    def test_country_selection_is_case_insensitive(self):
        prober = FakeProber()
        self.call("--country", "nl", prober=prober)
        self.assertEqual(prober.calls, [self.nl.pk])

    def test_ids_selection_overrides_the_filters(self):
        prober = FakeProber()
        self.call("--ids", str(self.disabled.pk), prober=prober)
        self.assertEqual(prober.calls, [self.disabled.pk])

    def test_stale_first_puts_never_checked_proxies_at_the_front(self):
        self.nl.last_checked = timezone.now()
        self.nl.save(update_fields=["last_checked"])
        prober = FakeProber()
        self.call("--stale-first", "--limit", "1", prober=prober)
        self.assertEqual(prober.calls, [self.de.pk])

    def test_dry_run_writes_nothing(self):
        self.call("--dry-run")
        self.assertEqual(ProxyProbe.objects.count(), 0)
        self.nl.refresh_from_db()
        self.assertEqual(self.nl.probe_count, 0)

    def test_running_twice_accumulates_rather_than_double_counting(self):
        self.call()
        self.call()
        self.nl.refresh_from_db()
        self.assertEqual(ProxyProbe.objects.filter(proxy=self.nl).count(), 2)
        self.assertEqual(self.nl.probe_count, 2)

    def test_failures_mark_the_proxy_dead_after_the_threshold(self):
        prober = FakeProber(default=ProbeResult.failed(ProbeErrorKind.TIMEOUT))
        for _ in range(3):
            self.call(prober=prober)
        self.nl.refresh_from_db()
        self.assertFalse(self.nl.is_alive)
        self.assertEqual(self.nl.consecutive_failures, 3)

    def test_unsupported_protocols_are_reported_and_left_unprobed(self):
        ss = make_proxy(
            host="203.0.113.9", protocol=ProxyProtocol.SHADOWSOCKS, port=8388
        )
        prober = FakeProber(unsupported={ProxyProtocol.SHADOWSOCKS.value})
        output = self.call(prober=prober)
        self.assertIn("skipped", output)
        ss.refresh_from_db()
        self.assertEqual(ss.probe_count, 0)
        self.assertTrue(ss.is_alive)
        self.assertEqual(ss.quality_score, UNPROBED_QUALITY_SCORE)

    def test_recompute_does_not_probe(self):
        write_probes(self.nl, [True] * 4)
        prober = FakeProber()
        self.call("--recompute", prober=prober)
        self.assertEqual(prober.calls, [])
        self.nl.refresh_from_db()
        self.assertEqual(self.nl.probe_count, 4)

    def test_a_bad_target_is_a_command_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.call("--target", "no-port-here")

    def test_bad_ids_are_a_command_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.call("--ids", "one,two")


class PruneProbesCommandTests(TestCase):
    def setUp(self):
        self.proxy = make_proxy()

    def call(self, *args, **options):
        out = StringIO()
        options.setdefault("stdout", out)
        call_command("prune_probes", *args, **options)
        return out.getvalue()

    def test_deletes_rows_past_the_retention_window(self):
        write_probes(
            self.proxy, [True] * 5, end=timezone.now() - timedelta(days=60)
        )
        write_probes(self.proxy, [True] * 5)
        self.call("--older-than-days", "30", "--keep-last", "0")
        self.assertEqual(ProxyProbe.objects.count(), 5)

    def test_keep_last_protects_a_rarely_probed_proxy(self):
        """A proxy whose entire history has aged out must not revert to
        'never probed' -- losing the evidence that it failed every time for
        six months is the opposite of useful."""
        write_probes(
            self.proxy, [False] * 4, end=timezone.now() - timedelta(days=90)
        )
        self.call("--older-than-days", "30", "--keep-last", "2")
        self.assertEqual(ProxyProbe.objects.count(), 2)

    def test_recent_rows_are_never_touched(self):
        write_probes(self.proxy, [True] * 10)
        self.call("--older-than-days", "30")
        self.assertEqual(ProxyProbe.objects.count(), 10)

    def test_dry_run_reports_without_deleting(self):
        write_probes(
            self.proxy, [True] * 5, end=timezone.now() - timedelta(days=60)
        )
        output = self.call(
            "--older-than-days", "30", "--keep-last", "0", "--dry-run"
        )
        self.assertIn("would delete 5", output)
        self.assertEqual(ProxyProbe.objects.count(), 5)

    def test_batching_deletes_everything(self):
        write_probes(
            self.proxy, [True] * 25, end=timezone.now() - timedelta(days=60)
        )
        self.call(
            "--older-than-days", "30", "--keep-last", "0", "--batch-size", "4"
        )
        self.assertEqual(ProxyProbe.objects.count(), 0)

    def test_retention_default_comes_from_settings(self):
        write_probes(
            self.proxy, [True] * 3, end=timezone.now() - timedelta(days=10)
        )
        with override_settings(PROXY_PROBE_RETENTION_DAYS=5, PROXY_PROBE_WINDOW=0):
            self.call()
        self.assertEqual(ProxyProbe.objects.count(), 0)

    def test_invalid_retention_is_refused(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.call("--older-than-days", "0")

    def test_each_affected_proxy_is_counted_once(self):
        """Found by running the command against seeded data: it reported
        ten affected proxies for a database holding five.

        `ProxyProbe` has a `Meta.ordering`, and Django adds ordering
        columns to the SELECT list of a `.distinct()`, so
        `DISTINCT proxy_id` became `DISTINCT proxy_id, checked_at, id`
        and de-duplicated nothing. Harmless to the data -- the per-proxy
        work is idempotent -- but it is one redundant query pair per
        surplus row, which on a real probe table is the difference
        between a prune and an outage.
        """
        other = make_proxy(host="203.0.113.99")
        for proxy in (self.proxy, other):
            write_probes(
                proxy, [True] * 5, end=timezone.now() - timedelta(days=60)
            )
        output = self.call("--older-than-days", "30", "--keep-last", "1")
        self.assertIn("(2 proxies had expired rows", output)

    def test_pruning_refreshes_the_health_cache_it_invalidated(self):
        """Found by running the command against seeded data.

        The health fields are a cache of the probe table, so deleting rows
        invalidates them. With a sane retention window nothing inside the
        scoring window is removed and the recompute is a no-op -- but
        `--older-than-days` can be passed shorter than the window on any
        given run, and without the recompute the proxy keeps reporting a
        success rate derived from rows that no longer exist, until the
        next probe run happens to correct it.
        """
        write_probes(
            self.proxy, [True] * 4, end=timezone.now() - timedelta(days=3)
        )
        recompute_health(self.proxy.pk)
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.probe_count, 4)

        self.call("--older-than-days", "1", "--keep-last", "0")

        self.proxy.refresh_from_db()
        self.assertEqual(ProxyProbe.objects.count(), 0)
        self.assertEqual(self.proxy.probe_count, 0)
        self.assertIsNone(self.proxy.success_rate)
        self.assertEqual(self.proxy.quality_score, UNPROBED_QUALITY_SCORE)

    def test_no_recompute_leaves_the_cache_alone(self):
        write_probes(
            self.proxy, [True] * 4, end=timezone.now() - timedelta(days=3)
        )
        recompute_health(self.proxy.pk)
        self.call(
            "--older-than-days", "1", "--keep-last", "0", "--no-recompute"
        )
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.probe_count, 4)

    def test_a_dry_run_does_not_recompute_either(self):
        write_probes(
            self.proxy, [True] * 4, end=timezone.now() - timedelta(days=3)
        )
        recompute_health(self.proxy.pk)
        self.call("--older-than-days", "1", "--keep-last", "0", "--dry-run")
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.probe_count, 4)
        self.assertEqual(ProxyProbe.objects.count(), 4)

    def test_keep_last_pruning_also_recomputes(self):
        write_probes(
            self.proxy, [True] * 6, end=timezone.now() - timedelta(days=3)
        )
        recompute_health(self.proxy.pk)
        self.call("--older-than-days", "1", "--keep-last", "2")
        self.proxy.refresh_from_db()
        self.assertEqual(ProxyProbe.objects.count(), 2)
        self.assertEqual(self.proxy.probe_count, 2)

    def test_pruning_then_recomputing_keeps_the_window_intact(self):
        """The invariant `proxies.W001` protects: as long as retention
        covers the window, pruning cannot change a proxy's score."""
        write_probes(self.proxy, [True] * 10)
        write_probes(
            self.proxy, [False] * 10, end=timezone.now() - timedelta(days=60)
        )
        with override_settings(PROXY_PROBE_WINDOW=10, PROXY_PROBE_WINDOW_DAYS=7):
            before = recompute_health(self.proxy.pk)
            self.call("--older-than-days", "30", "--keep-last", "0")
            after = recompute_health(self.proxy.pk)
        self.assertEqual(before.as_update(), after.as_update())


# ---------------------------------------------------------------------------
# System checks
# ---------------------------------------------------------------------------


class HealthCheckTests(TestCase):
    @override_settings(PROXY_PROBE_RETENTION_DAYS=3, PROXY_PROBE_WINDOW_DAYS=7)
    def test_retention_shorter_than_the_window_is_flagged(self):
        warnings = check_probe_retention_covers_window(None)
        self.assertEqual([warning.id for warning in warnings], ["proxies.W001"])

    @override_settings(PROXY_PROBE_RETENTION_DAYS=30, PROXY_PROBE_WINDOW_DAYS=7)
    def test_a_sane_retention_passes(self):
        self.assertEqual(check_probe_retention_covers_window(None), [])

    @override_settings(PROXY_DEAD_AFTER_FAILURES=1)
    def test_a_threshold_of_one_is_flagged(self):
        ids = [warning.id for warning in check_health_thresholds(None)]
        self.assertIn("proxies.W002", ids)

    @override_settings(PROXY_PROBE_WINDOW=1)
    def test_a_window_of_one_is_flagged(self):
        ids = [warning.id for warning in check_health_thresholds(None)]
        self.assertIn("proxies.W003", ids)

    @override_settings(PROXY_LATENCY_WEIGHT=1.5)
    def test_an_out_of_range_weight_is_flagged(self):
        ids = [warning.id for warning in check_health_thresholds(None)]
        self.assertIn("proxies.W004", ids)

    @override_settings(PROXY_LATENCY_WEIGHT=0.9)
    def test_a_weight_that_inverts_the_model_is_flagged(self):
        ids = [warning.id for warning in check_health_thresholds(None)]
        self.assertIn("proxies.W005", ids)

    def test_the_shipped_defaults_are_clean(self):
        self.assertEqual(check_probe_retention_covers_window(None), [])
        self.assertEqual(check_health_thresholds(None), [])
