"""Admin API: health on the proxy list, and the probe trigger.

Reuses `adminapi.tests.AdminApiTestCase` so the staff/non-staff/superuser
fixtures and the real-JWT helpers stay in one place.

The probe endpoint is driven by `FakeProber` throughout. The point of a
pluggable prober is precisely that the admin endpoint, the management
command and the tests all go through `get_prober()`, so nothing here needs
to reach a network to prove the endpoint works.
"""

from unittest import mock

from django.test import override_settings
from rest_framework import status

from adminapi.tests import AdminApiTestCase, admin_endpoints
from proxies.models import (
    ProbeErrorKind,
    Proxy,
    ProxyProbe,
    ProxyProtocol,
    ProxyTier,
)
from proxies.probing import FakeProber, ProbeResult

PROBE_URL = "/api/admin/proxies/probe/"

HEALTH_FIELDS = (
    "is_alive",
    "latency_ms",
    "last_checked",
    "success_rate",
    "probe_count",
    "consecutive_failures",
    "latency_p50_ms",
    "latency_p95_ms",
    "quality_score",
)
CREDENTIAL_KEYS = ("username", "password", "ss_method")


class AdminProxyHealthFieldsTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)
        Proxy.objects.filter(pk=self.premium_proxy.pk).update(
            success_rate=0.92,
            probe_count=25,
            consecutive_failures=1,
            latency_p50_ms=60,
            latency_p95_ms=180,
            quality_score=78.5,
        )

    def test_the_list_carries_health(self):
        rows = self.client.get("/api/admin/proxies/").data["results"]
        row = next(r for r in rows if r["id"] == self.premium_proxy.pk)
        for field in HEALTH_FIELDS:
            self.assertIn(field, row)
        self.assertEqual(row["success_rate"], 0.92)
        self.assertEqual(row["latency_p95_ms"], 180)
        self.assertEqual(row["quality_score"], 78.5)

    def test_health_does_not_drag_credentials_into_the_list(self):
        rows = self.client.get("/api/admin/proxies/").data["results"]
        for row in rows:
            for key in CREDENTIAL_KEYS:
                self.assertNotIn(key, row)

    def test_health_fields_are_read_only(self):
        """They are derived from ProxyProbe; a hand-typed success rate
        would be overwritten by the next probe run, which reads as the
        admin losing writes."""
        response = self.client.patch(
            f"/api/admin/proxies/{self.premium_proxy.pk}/",
            {"success_rate": 0.01, "quality_score": 1.0, "probe_count": 999},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.premium_proxy.refresh_from_db()
        self.assertEqual(self.premium_proxy.success_rate, 0.92)
        self.assertEqual(self.premium_proxy.quality_score, 78.5)
        self.assertEqual(self.premium_proxy.probe_count, 25)

    def test_filtering_by_success_rate(self):
        response = self.client.get("/api/admin/proxies/?min_success_rate=0.9")
        self.assertEqual(
            [row["id"] for row in response.data["results"]], [self.premium_proxy.pk]
        )

    def test_filtering_by_quality_score_range(self):
        response = self.client.get(
            "/api/admin/proxies/?min_quality_score=70&max_quality_score=80"
        )
        self.assertEqual(
            [row["id"] for row in response.data["results"]], [self.premium_proxy.pk]
        )

    def test_the_unprobed_filter_finds_what_nothing_has_measured(self):
        """A different and more actionable question than 'what is dead'."""
        response = self.client.get("/api/admin/proxies/?unprobed=true")
        ids = {row["id"] for row in response.data["results"]}
        self.assertNotIn(self.premium_proxy.pk, ids)
        self.assertIn(self.free_proxy.pk, ids)

        response = self.client.get("/api/admin/proxies/?unprobed=false")
        self.assertEqual(
            [row["id"] for row in response.data["results"]], [self.premium_proxy.pk]
        )

    def test_filtering_by_consecutive_failures(self):
        Proxy.objects.filter(pk=self.free_proxy.pk).update(consecutive_failures=7)
        response = self.client.get("/api/admin/proxies/?min_consecutive_failures=5")
        self.assertEqual(
            [row["id"] for row in response.data["results"]], [self.free_proxy.pk]
        )

    def test_ordering_by_quality_score(self):
        response = self.client.get("/api/admin/proxies/?ordering=-quality_score")
        self.assertEqual(response.data["results"][0]["id"], self.premium_proxy.pk)


@override_settings(PROXY_PROBER="proxies.probing.FakeProber")
class AdminProbeEndpointTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def probe(self, ids, prober=None, client=None):
        client = client or self.client
        if prober is None:
            return client.post(PROBE_URL, {"ids": ids}, format="json")
        with mock.patch("adminapi.views.get_prober", return_value=prober):
            return client.post(PROBE_URL, {"ids": ids}, format="json")

    def test_a_probe_run_writes_rows_and_returns_refreshed_health(self):
        response = self.probe([self.free_proxy.pk, self.premium_proxy.pk])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["requested"], 2)
        self.assertEqual(response.data["probed"], 2)
        self.assertEqual(response.data["succeeded"], 2)
        self.assertEqual(ProxyProbe.objects.count(), 2)

        row = response.data["results"][0]
        self.assertTrue(row["succeeded"])
        self.assertEqual(row["health"]["probe_count"], 1)
        self.assertEqual(row["health"]["success_rate"], 1.0)

    def test_a_failure_is_reported_with_its_error_kind(self):
        prober = FakeProber(
            default=ProbeResult.failed(ProbeErrorKind.AUTH_FAILED, "407")
        )
        response = self.probe([self.free_proxy.pk], prober=prober)
        self.assertEqual(response.data["failed"], 1)
        self.assertEqual(
            response.data["results"][0]["error_kind"], ProbeErrorKind.AUTH_FAILED
        )
        self.assertIsNone(response.data["results"][0]["latency_ms"])

    def test_three_failures_mark_the_proxy_dead_through_the_endpoint(self):
        prober = FakeProber(default=ProbeResult.failed(ProbeErrorKind.TIMEOUT))
        for _ in range(3):
            self.probe([self.free_proxy.pk], prober=prober)
        self.free_proxy.refresh_from_db()
        self.assertFalse(self.free_proxy.is_alive)
        self.assertEqual(self.free_proxy.consecutive_failures, 3)

    def test_one_failure_does_not(self):
        prober = FakeProber(default=ProbeResult.failed(ProbeErrorKind.TIMEOUT))
        self.probe([self.free_proxy.pk], prober=prober)
        self.free_proxy.refresh_from_db()
        self.assertTrue(self.free_proxy.is_alive)

    def test_unsupported_protocols_are_skipped_not_recorded_as_dead(self):
        prober = FakeProber(unsupported={ProxyProtocol.SHADOWSOCKS.value})
        response = self.probe([self.ss_proxy.pk], prober=prober)
        self.assertEqual(response.data["probed"], 0)
        self.assertEqual(
            [row["proxy"] for row in response.data["skipped"]], [self.ss_proxy.pk]
        )
        self.ss_proxy.refresh_from_db()
        self.assertTrue(self.ss_proxy.is_alive)
        self.assertEqual(self.ss_proxy.probe_count, 0)
        self.assertFalse(ProxyProbe.objects.filter(proxy=self.ss_proxy).exists())

    def test_a_prober_bug_is_reported_as_ours_not_the_proxys(self):
        class Exploding(FakeProber):
            def probe(self, target, **kwargs):
                raise RuntimeError("boom")

        with self.assertLogs("proxycrack.probing", level="ERROR"):
            response = self.probe([self.free_proxy.pk], prober=Exploding())
        self.assertEqual(response.data["probed"], 0)
        self.assertEqual(len(response.data["errored"]), 1)
        self.free_proxy.refresh_from_db()
        self.assertTrue(self.free_proxy.is_alive)
        self.assertEqual(ProxyProbe.objects.count(), 0)

    def test_unknown_ids_are_reported_rather_than_silently_dropped(self):
        response = self.probe([self.free_proxy.pk, 999999])
        self.assertEqual(response.data["not_found"], [999999])
        self.assertEqual(response.data["probed"], 1)

    def test_a_disabled_proxy_can_still_be_probed_by_id(self):
        """The operator may be deciding whether to re-enable it."""
        Proxy.objects.filter(pk=self.free_proxy.pk).update(is_enabled=False)
        response = self.probe([self.free_proxy.pk])
        self.assertEqual(response.data["probed"], 1)

    def test_duplicate_ids_are_probed_once(self):
        response = self.probe([self.free_proxy.pk, self.free_proxy.pk])
        self.assertEqual(response.data["requested"], 1)
        self.assertEqual(ProxyProbe.objects.count(), 1)

    @override_settings(PROXY_PROBE_API_MAX_IDS=2)
    def test_the_id_list_is_capped_far_below_the_bulk_cap(self):
        """This endpoint probes synchronously in a request worker."""
        response = self.probe([1, 2, 3])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ids", response.data["error"]["detail"])

    def test_an_empty_id_list_is_refused(self):
        response = self.client.post(PROBE_URL, {"ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_non_integer_id_is_refused(self):
        response = self.client.post(PROBE_URL, {"ids": ["abc"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_run_is_audited(self):
        with self.assertLogs("proxycrack.audit", level="INFO") as captured:
            self.probe([self.free_proxy.pk])
        self.assertTrue(
            any("admin_probe_run" in line for line in captured.output),
            captured.output,
        )

    def test_a_non_staff_token_is_refused(self):
        client = self.client_class()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.access_token(self.member)}"
        )
        response = client.post(PROBE_URL, {"ids": [1]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ProxyProbe.objects.count(), 0)

    def test_a_mobile_token_is_refused(self):
        client = self.client_class()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.mobile_access_token(self.staff)}"
        )
        response = client.post(PROBE_URL, {"ids": [1]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_refused(self):
        client = self.client_class()
        response = client.post(PROBE_URL, {"ids": [1]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_the_probe_budget_actually_runs_out(self):
        """A resource control rather than an anti-abuse one: each call
        holds a worker open while it opens outbound connections.

        The class attribute is patched rather than the setting, for the
        reason `test_the_credential_budget_actually_runs_out` documents:
        `SimpleRateThrottle.THROTTLE_RATES` is bound to the settings dict
        at import time, so `override_settings` cannot reach it.
        """
        from adminapi.throttling import AdminProbeThrottle

        with mock.patch.object(AdminProbeThrottle, "rate", "2/hour", create=True):
            codes = [
                self.probe([self.free_proxy.pk]).status_code for _ in range(4)
            ]
        self.assertEqual(codes[:2], [status.HTTP_200_OK] * 2)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_the_probe_scope_is_configured(self):
        """A throttle class whose scope has no rate silently allows
        everything."""
        from django.conf import settings as django_settings

        rates = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("admin_probe", rates)
        self.assertTrue(rates["admin_probe"])

    def test_a_throttled_call_probes_nothing(self):
        """The refusal must happen before any outbound connection, or the
        limit bounds the response and not the work."""
        from adminapi.throttling import AdminProbeThrottle

        with mock.patch.object(AdminProbeThrottle, "rate", "1/hour", create=True):
            self.probe([self.free_proxy.pk])
            prober = FakeProber()
            response = self.probe([self.premium_proxy.pk], prober=prober)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(prober.calls, [])

    def test_the_route_is_in_the_staff_gate_sweep(self):
        """The `adminapi.E001` boot check and the permission sweep both
        walk the live URLconf, so a new action is covered automatically --
        this asserts the route is actually there to be swept."""
        urls = {url for url, _methods, _name in admin_endpoints()}
        self.assertIn(PROBE_URL, urls)


class AdminChainWithConstrainedHopTests(AdminApiTestCase):
    """`GET /api/admin/chains/` embeds the hop's proxy. A constrained hop
    has none, so the nested serializer must render null rather than 500.

    Worth its own test because the admin chain serializer is read-only and
    predates constrained hops entirely -- nothing in it was written with a
    null `proxy` in mind.
    """

    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)
        from proxies.models import ChainHop

        ChainHop.objects.create(
            chain=self.chain, position=1, country="NL", max_latency_ms=200
        )

    def test_the_admin_chain_list_renders_a_constrained_hop(self):
        response = self.client.get("/api/admin/chains/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = next(r for r in response.data["results"] if r["id"] == self.chain.pk)
        hops = sorted(row["hops"], key=lambda h: h["position"])
        self.assertIsNotNone(hops[0]["proxy"])
        self.assertIsNone(hops[1]["proxy"])
        self.assertEqual(row["length"], 2)

    def test_the_admin_chain_detail_renders_it_too(self):
        response = self.client.get(f"/api/admin/chains/{self.chain.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["hops"]), 2)
