"""Admin API: IP intelligence and candidate evaluation.

The endpoint this file is really about is `POST /api/admin/intel/candidates/`
-- the operator's stated use case. Paste in candidate endpoints from three
vendors and find out whether they are three operators or one wearing three
brands, **before** paying for a year of all three. The hosts therefore need
not exist in the inventory, and a test proves that nothing is written to
`Proxy`.

Everything goes through `FakeIpIntel`. Reuses `adminapi.tests.AdminApiTestCase`
so the staff/non-staff fixtures and the real-JWT helpers stay in one place;
the staff sweep in that module picks up these routes automatically.
"""

from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from adminapi.models import AdminAuditLog
from adminapi.tests import AdminApiTestCase, admin_endpoints
from proxies.intel import FakeIpIntel, IpFacts
from proxies.models import (
    ChainHop,
    IpIntelRecord,
    IpIntelStatus,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
)

CANDIDATES_URL = "/api/admin/intel/candidates/"
REFRESH_URL = "/api/admin/intel/refresh/"
RECORDS_URL = "/api/admin/intel/"


def facts(host, asn, org, prefix, country="NL"):
    return IpFacts(
        query=host,
        ip=host,
        asn=asn,
        asn_org=org,
        prefix=prefix,
        country=country,
        registry="ripencc",
        status=IpIntelStatus.OK,
        source="fake",
    )


class IntelApiTestCase(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)
        # Three candidate addresses from "three vendors". Two of them are
        # in fact one operator's /16 -- which is the whole point.
        self.intel = FakeIpIntel(
            {
                "203.0.113.10": facts("203.0.113.10", 64500, "Acme", "203.0.113.0/24"),
                "203.0.113.11": facts("203.0.113.11", 64500, "Acme", "203.0.113.0/24"),
                "198.51.100.40": facts(
                    "198.51.100.40", 64600, "Beta", "198.51.100.0/24", country="BR"
                ),
            }
        )

    def post(self, url, payload, intel=None):
        with mock.patch(
            "adminapi.views.get_ip_intel", return_value=intel or self.intel
        ):
            return self.client.post(url, payload, format="json")

    @staticmethod
    def codes(payload):
        return [finding["code"] for finding in payload["findings"]]


class CandidateEvaluationTests(IntelApiTestCase):
    def payload(self, **overrides):
        body = {
            "candidates": [
                {"host": "203.0.113.10", "provider": "Vendor One"},
                {"host": "198.51.100.40", "provider": "Vendor Two"},
                {"host": "203.0.113.11", "provider": "Vendor Three"},
            ]
        }
        body.update(overrides)
        return body

    def test_it_analyses_hosts_that_are_not_in_the_inventory(self):
        """The requirement, stated as plainly as it can be: no Proxy rows
        exist for these addresses before or after."""
        self.assertFalse(Proxy.objects.filter(host="203.0.113.10").exists())

        response = self.post(CANDIDATES_URL, self.payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(Proxy.objects.filter(host__startswith="203.0.113.1").exists())
        self.assertEqual(len(response.data["hops"]), 3)

    def test_it_finds_the_two_vendors_that_are_one_operator(self):
        response = self.post(CANDIDATES_URL, self.payload())

        payload = response.data
        # Hops 0 and 2 are entry and exit, same /24, sold under two
        # different vendor names. Exactly the purchase this endpoint is
        # meant to stop.
        self.assertIn("entry_exit_shared_network", self.codes(payload))
        self.assertEqual(payload["worst_severity"], "critical")
        self.assertLess(payload["score"], 50)
        self.assertEqual(payload["counts"]["asns"], 2)
        self.assertEqual(payload["counts"]["providers"], 3)

    def test_real_vendor_names_are_shown_to_the_operator(self):
        """The opposite of the mobile endpoint: the operator typed these
        in and is the one deciding what to buy."""
        payload = self.post(CANDIDATES_URL, self.payload()).data
        self.assertIn("Vendor One", str(payload))
        self.assertNotIn("Vendor A", str(payload))

    def test_the_lookups_are_cached_and_reported(self):
        first = self.post(CANDIDATES_URL, self.payload()).data
        self.assertEqual(sorted(first["lookups"]["looked_up"]), [
            "198.51.100.40",
            "203.0.113.10",
            "203.0.113.11",
        ])
        self.assertEqual(first["lookups"]["from_cache"], [])

        self.intel.calls.clear()
        second = self.post(CANDIDATES_URL, self.payload()).data

        self.assertEqual(self.intel.calls, [])
        self.assertEqual(len(second["lookups"]["from_cache"]), 3)
        self.assertEqual(IpIntelRecord.objects.count(), 3)

    def test_refresh_true_forces_a_re_query(self):
        self.post(CANDIDATES_URL, self.payload())
        self.intel.calls.clear()

        self.post(CANDIDATES_URL, self.payload(refresh=True))

        self.assertEqual(len(self.intel.calls), 3)

    def test_an_unknown_candidate_is_reported_as_unknown_not_as_different(self):
        body = {
            "candidates": [
                {"host": "203.0.113.10", "provider": "One"},
                {"host": "192.0.2.99", "provider": "Two"},
            ]
        }

        payload = self.post(CANDIDATES_URL, body).data

        self.assertIn("hop_intel_unknown", self.codes(payload))
        self.assertEqual(payload["worst_severity"], "unknown")
        self.assertEqual(payload["coverage"]["known"], 1)
        self.assertLess(payload["score"], 100)

    def test_an_unavailable_source_is_reported_as_ours(self):
        payload = self.post(
            CANDIDATES_URL, self.payload(), intel=FakeIpIntel(unavailable=True)
        ).data

        self.assertTrue(payload["lookups"]["unavailable"])
        self.assertEqual(payload["worst_severity"], "unknown")

    def test_the_candidate_list_is_capped(self):
        body = {
            "candidates": [
                {"host": f"203.0.113.{index}"} for index in range(1, 60)
            ]
        }
        response = self.post(CANDIDATES_URL, body)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("candidates", str(response.data))

    def test_a_url_is_not_a_host(self):
        """This feeds getaddrinfo and a DNS query. Staff-only and
        throttled is not a reason to accept a connection string."""
        for bad in (
            "http://internal/admin",
            "user:pass@10.0.0.1",
            "10.0.0.1:1080/x",
            "",
        ):
            response = self.post(CANDIDATES_URL, {"candidates": [{"host": bad}]})
            self.assertEqual(
                response.status_code,
                status.HTTP_400_BAD_REQUEST,
                f"{bad!r} was accepted",
            )

    def test_an_empty_candidate_list_is_refused(self):
        response = self.post(CANDIDATES_URL, {"candidates": []})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_it_is_audit_logged(self):
        self.post(CANDIDATES_URL, self.payload())

        entry = AdminAuditLog.objects.get(action="admin_intel_candidates")
        self.assertEqual(entry.actor_id, self.staff.pk)
        self.assertEqual(entry.payload["candidates"], 3)
        self.assertEqual(entry.payload["worst_severity"], "critical")
        self.assertIn("entry_exit_shared_network", entry.payload["finding_codes"])
        self.assertIn("203.0.113.10", entry.payload["hosts"])

    def test_the_error_envelope_is_unchanged(self):
        response = self.post(CANDIDATES_URL, {"candidates": []})
        self.assertIn("error", response.data)
        for key in ("code", "status", "detail"):
            self.assertIn(key, response.data["error"])

    def test_a_non_staff_token_is_refused(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.mobile_access_token(self.member)}"
        )
        response = self.client.post(CANDIDATES_URL, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_route_is_in_the_staff_sweep_inventory(self):
        """Guards against the route being added in a way the automatic
        permission sweep cannot see."""
        urls = {url for url, _methods, _name in admin_endpoints()}
        self.assertIn(CANDIDATES_URL, urls)
        self.assertIn(REFRESH_URL, urls)


class IntelRefreshTests(IntelApiTestCase):
    def setUp(self):
        super().setUp()
        self.proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="203.0.113.10",
            port=1080,
            tier=ProxyTier.PREMIUM,
            provider="Acme",
        )

    def test_refresh_by_proxy_id(self):
        response = self.post(REFRESH_URL, {"ids": [self.proxy.pk]})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["looked_up"], ["203.0.113.10"])
        record = IpIntelRecord.objects.get(query="203.0.113.10")
        self.assertEqual(record.asn, 64500)
        self.assertEqual(response.data["records"][0]["asn"], 64500)

    def test_refresh_by_bare_host(self):
        response = self.post(REFRESH_URL, {"hosts": ["198.51.100.40"]})

        self.assertEqual(response.data["looked_up"], ["198.51.100.40"])
        self.assertFalse(Proxy.objects.filter(host="198.51.100.40").exists())

    def test_unknown_ids_are_reported_not_silently_dropped(self):
        response = self.post(REFRESH_URL, {"ids": [self.proxy.pk, 999999]})
        self.assertEqual(response.data["not_found"], [999999])

    def test_a_fresh_record_is_not_re_queried_without_force(self):
        self.post(REFRESH_URL, {"ids": [self.proxy.pk]})
        self.intel.calls.clear()

        response = self.post(REFRESH_URL, {"ids": [self.proxy.pk]})

        self.assertEqual(self.intel.calls, [])
        self.assertEqual(response.data["from_cache"], ["203.0.113.10"])

    def test_force_re_queries(self):
        self.post(REFRESH_URL, {"ids": [self.proxy.pk]})
        self.intel.calls.clear()
        self.post(REFRESH_URL, {"ids": [self.proxy.pk], "force": True})
        self.assertEqual(self.intel.calls, ["203.0.113.10"])

    def test_a_selection_resolving_to_too_many_hosts_is_refused(self):
        """`ids` is capped at MAX_IDS, but a thousand ids can be a thousand
        distinct hosts and this runs in a request worker."""
        ids = [self.proxy.pk]
        for index in range(1, 40):
            ids.append(
                Proxy.objects.create(
                    protocol=ProxyProtocol.SOCKS5,
                    host=f"192.0.2.{index}",
                    port=1080,
                ).pk
            )

        response = self.post(REFRESH_URL, {"ids": ids})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("distinct hosts", str(response.data))

    def test_many_proxies_on_one_host_are_one_lookup(self):
        for port in (1081, 1082):
            Proxy.objects.create(
                protocol=ProxyProtocol.SOCKS5, host="203.0.113.10", port=port
            )
        ids = list(Proxy.objects.filter(host="203.0.113.10").values_list("pk", flat=True))

        self.post(REFRESH_URL, {"ids": ids})

        self.assertEqual(self.intel.calls, ["203.0.113.10"])

    def test_a_body_with_neither_ids_nor_hosts_is_refused(self):
        response = self.post(REFRESH_URL, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_it_is_audit_logged(self):
        self.post(REFRESH_URL, {"ids": [self.proxy.pk]})

        entry = AdminAuditLog.objects.get(action="admin_intel_refresh")
        self.assertEqual(entry.payload["looked_up"], 1)
        # `proxy_ids` (plural) like the bulk endpoints, so `target_type`
        # stays empty -- a refresh of forty rows has no single target and
        # inventing one would misrepresent the event.
        self.assertEqual(entry.payload["proxy_ids"], [self.proxy.pk])
        self.assertEqual(entry.actor_id, self.staff.pk)


class IntelRecordListTests(IntelApiTestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        IpIntelRecord.objects.create(
            query="203.0.113.10",
            ip="203.0.113.10",
            asn=64500,
            asn_org="Acme",
            prefix="203.0.113.0/24",
            country="NL",
            registry="ripencc",
            status=IpIntelStatus.OK,
            source="fake",
            looked_up_at=now,
            expires_at=now + timedelta(days=30),
        )
        IpIntelRecord.objects.create(
            query="192.0.2.99",
            status=IpIntelStatus.NOT_FOUND,
            source="fake",
            looked_up_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )

    def test_the_list_is_readable_by_staff(self):
        response = self.client.get(RECORDS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)

    def test_stale_filter(self):
        rows = self.client.get(f"{RECORDS_URL}?stale=true").data["results"]
        self.assertEqual([row["query"] for row in rows], ["192.0.2.99"])

    def test_unknown_filter(self):
        rows = self.client.get(f"{RECORDS_URL}?unknown=true").data["results"]
        self.assertEqual([row["query"] for row in rows], ["192.0.2.99"])

    def test_asn_filter(self):
        rows = self.client.get(f"{RECORDS_URL}?asn=64500").data["results"]
        self.assertEqual([row["query"] for row in rows], ["203.0.113.10"])

    def test_rows_carry_freshness_and_knownness_separately(self):
        rows = {row["query"]: row for row in self.client.get(RECORDS_URL).data["results"]}
        self.assertTrue(rows["203.0.113.10"]["is_fresh"])
        self.assertTrue(rows["203.0.113.10"]["is_known"])
        self.assertFalse(rows["192.0.2.99"]["is_fresh"])
        self.assertFalse(rows["192.0.2.99"]["is_known"])

    def test_records_are_not_writable(self):
        """A hand-typed ASN would make the analysis report a correlation
        nobody measured."""
        response = self.client.post(
            RECORDS_URL, {"query": "1.2.3.4", "asn": 1}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class AdminProxyProviderTests(IntelApiTestCase):
    def test_provider_is_on_the_list_and_writable(self):
        response = self.client.patch(
            f"/api/admin/proxies/{self.premium_proxy.pk}/",
            {"provider": "Webshare"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.premium_proxy.refresh_from_db()
        self.assertEqual(self.premium_proxy.provider, "Webshare")

        rows = self.client.get("/api/admin/proxies/").data["results"]
        row = next(item for item in rows if item["id"] == self.premium_proxy.pk)
        self.assertEqual(row["provider"], "Webshare")

    def test_provider_filters(self):
        Proxy.objects.filter(pk=self.premium_proxy.pk).update(provider="Webshare")

        matched = self.client.get("/api/admin/proxies/?provider=webshare").data
        self.assertEqual([row["id"] for row in matched["results"]], [self.premium_proxy.pk])

        unlabelled = self.client.get("/api/admin/proxies/?has_provider=false").data
        self.assertNotIn(
            self.premium_proxy.pk, [row["id"] for row in unlabelled["results"]]
        )

    def test_provider_is_not_exposed_on_the_mobile_proxy_list(self):
        """P4: the operator's supply chain is not a field the subscriber
        base gets to read."""
        from api.serializers import ProxyListSerializer

        self.assertNotIn("provider", ProxyListSerializer.Meta.fields)


class AdminChainDiversityTests(IntelApiTestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        for host, asn in (("198.51.100.2", 64500),):
            IpIntelRecord.objects.create(
                query=host,
                ip=host,
                asn=asn,
                asn_org="Acme",
                prefix="198.51.100.0/24",
                country="DE",
                status=IpIntelStatus.OK,
                source="fake",
                looked_up_at=now,
                expires_at=now + timedelta(days=30),
            )

    def test_it_returns_the_same_analysis_with_real_vendor_names(self):
        Proxy.objects.filter(pk=self.premium_proxy.pk).update(provider="Webshare")

        response = self.client.get(f"/api/admin/chains/{self.chain.pk}/diversity/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["chain"]["user"], self.member.pk)
        self.assertIn("single_hop", [f["code"] for f in response.data["findings"]])

    def test_constrained_positions_are_reported_unresolved_not_filled_in(self):
        """An admin read must not depend on the subscriber's entitlement
        still being valid, so the chain is deliberately not resolved."""
        ChainHop.objects.create(chain=self.chain, position=1, country="NL")

        response = self.client.get(f"/api/admin/chains/{self.chain.pk}/diversity/")

        codes = [f["code"] for f in response.data["findings"]]
        self.assertIn("hop_unresolved", codes)
