"""Admin API: provider sync (R9).

`POST /api/admin/proxies/sync/` and the `sync-runs` history beside it.

Everything goes through `FakeProviderSync` -- CI cannot reach Webshare,
and an admin test that did would be a test that fails when a vendor has a
bad morning. Reuses `adminapi.tests.AdminApiTestCase`, so the staff and
non-staff fixtures stay in one place and the URLconf sweep in that module
picks these routes up automatically.

The assertions that matter are about what must *not* happen: a non-staff
token must not reach it, a default call must not write, and the API token
must not reach the response, the audit payload, the stored run row or a
log line.
"""

import logging
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status

from adminapi.models import AdminAuditLog
from adminapi.tests import AdminApiTestCase
from proxies.models import (
    IpIntelRecord,
    ProviderSyncRun,
    ProviderSyncStatus,
    Proxy,
    ProxyProtocol,
    ProxyTier,
)
from proxies.providers import FakeProviderSync, ProviderProxy
from proxies.test_providers import FAKE_TOKEN, capture_all_logs

SYNC_URL = "/api/admin/proxies/sync/"
RUNS_URL = "/api/admin/sync-runs/"


def record(external_id="1001", host="45.38.107.97", port=6014, **kwargs):
    defaults = {
        "username": "user1",
        "password": "pass1",
        "country": "GB",
        "city": "London",
        "asn": 20738,
        "asn_org": "Heart Internet Limited",
    }
    defaults.update(kwargs)
    return ProviderProxy(
        external_id=external_id,
        host=host,
        port=port,
        protocol=ProxyProtocol.HTTP,
        **defaults,
    )


class ProviderSyncApiTestCase(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)
        self.adapter = FakeProviderSync([record()], provider="webshare")

    def post(self, payload=None, adapter=None):
        with mock.patch(
            "adminapi.views.get_provider_sync", return_value=adapter or self.adapter
        ):
            return self.client.post(SYNC_URL, payload or {}, format="json")


class SyncEndpointTests(ProviderSyncApiTestCase):
    def test_it_defaults_to_a_dry_run(self):
        """The opposite default to every other action on this viewset.

        Reconciling is the only admin operation that can disable a large
        slice of the sellable inventory on the strength of a third party's
        HTTP response, so the rehearsal is what omission buys.
        """
        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["dry_run"])
        self.assertEqual(response.data["counts"]["created"], 1)
        self.assertFalse(Proxy.objects.filter(external_id="1001").exists())

    def test_dry_run_false_commits(self):
        response = self.post({"dry_run": False})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        proxy = Proxy.objects.get(external_id="1001")
        self.assertEqual(proxy.provider, "webshare")
        self.assertEqual(proxy.country, "GB")

    def test_the_response_carries_the_run_row(self):
        response = self.post({"dry_run": False})
        run = ProviderSyncRun.objects.get()
        self.assertEqual(response.data["run"]["id"], run.pk)
        self.assertEqual(response.data["run"]["triggered_by"], self.staff.email)
        self.assertEqual(response.data["status"], ProviderSyncStatus.OK)

    def test_per_record_results_name_fields_never_values(self):
        response = self.post({"dry_run": False})
        result = response.data["results"][0]
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["endpoint"], "45.38.107.97:6014")
        self.assertIn("password", result["fields"])
        # The name of the column, not what is in it.
        self.assertNotIn("pass1", str(response.data))
        self.assertNotIn("user1", str(response.data))

    def test_an_incomplete_listing_is_flagged_in_the_response(self):
        """A tally of zero disabled reads as "nothing vanished" unless
        the response says the listing was never complete."""
        adapter = FakeProviderSync([record()], provider="webshare", complete=False)
        response = self.post({"dry_run": False}, adapter=adapter)

        self.assertFalse(response.data["complete"])
        self.assertEqual(response.data["status"], ProviderSyncStatus.PARTIAL)

    def test_it_fills_ip_intel_by_default(self):
        self.post({"dry_run": False})
        intel = IpIntelRecord.objects.get(query="45.38.107.97")
        self.assertEqual(intel.asn, 20738)
        self.assertEqual(intel.source, "provider:fake")

    def test_write_intel_false_skips_the_fill(self):
        self.post({"dry_run": False, "write_intel": False})
        self.assertFalse(IpIntelRecord.objects.exists())

    def test_limit_is_capped(self):
        with override_settings(PROVIDER_SYNC_API_MAX_RECORDS=10):
            response = self.post({"limit": 5000})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("limit", str(response.data))
        self.assertIn("sync_webshare", str(response.data))

    def test_a_missing_token_is_503_not_500(self):
        """Correctly configured code that has not been given a
        credential. The message names the setting and not its value."""
        adapter = FakeProviderSync(missing_credentials=True)
        response = self.post({"dry_run": False}, adapter=adapter)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "provider_credentials_missing")

    def test_an_unavailable_provider_is_502(self):
        adapter = FakeProviderSync(unavailable="connection reset")
        response = self.post({"dry_run": False}, adapter=adapter)
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["code"], "provider_unavailable")

    def test_a_vanished_endpoint_is_disabled_not_deleted(self):
        self.post({"dry_run": False})
        proxy = Proxy.objects.get(external_id="1001")

        empty = FakeProviderSync([], provider="webshare")
        response = self.post({"dry_run": False}, adapter=empty)

        self.assertEqual(response.data["counts"]["disabled"], 1)
        proxy.refresh_from_db()
        self.assertFalse(proxy.is_enabled)
        self.assertTrue(Proxy.objects.filter(pk=proxy.pk).exists())


class SyncAuditTests(ProviderSyncApiTestCase):
    def test_a_sync_is_audited(self):
        self.post({"dry_run": False})
        row = AdminAuditLog.objects.get(action="admin_provider_sync")
        self.assertEqual(row.actor_id, self.staff.pk)
        self.assertEqual(row.payload["provider"], "webshare")
        self.assertEqual(row.payload["created"], 1)
        self.assertFalse(row.payload["dry_run"])

    def test_a_dry_run_is_audited_as_one(self):
        self.post()
        row = AdminAuditLog.objects.get(action="admin_provider_sync")
        self.assertTrue(row.payload["dry_run"])

    def test_a_failure_is_audited(self):
        adapter = FakeProviderSync(missing_credentials=True)
        self.post({"dry_run": False}, adapter=adapter)
        row = AdminAuditLog.objects.get(action="admin_provider_sync_failed")
        self.assertEqual(row.payload["reason"], "credentials_missing")

    def test_no_audit_payload_contains_the_api_token(self):
        """The whole point of the sync endpoint's audit line is that it
        records the *access*, never the credential -- the same rule the
        credential-reveal endpoint follows."""
        with override_settings(WEBSHARE_API_TOKEN=FAKE_TOKEN):
            self.post({"dry_run": False})
        for row in AdminAuditLog.objects.all():
            self.assertNotIn(FAKE_TOKEN, str(row.payload))
            self.assertNotIn("pass1", str(row.payload))

    def test_no_log_line_contains_the_api_token(self):
        with override_settings(WEBSHARE_API_TOKEN=FAKE_TOKEN):
            with capture_all_logs() as captured:
                self.post({"dry_run": False})
        self.assertNotIn(FAKE_TOKEN, captured.text)
        self.assertNotIn("pass1", captured.text)

    def test_the_stored_run_row_contains_no_credential(self):
        adapter = FakeProviderSync(
            [record(), record("1001", host="10.0.0.9")], provider="webshare"
        )
        self.post({"dry_run": False}, adapter=adapter)
        run = ProviderSyncRun.objects.get()
        self.assertTrue(run.errors)  # the duplicate id was reported
        self.assertNotIn("pass1", run.errors)
        self.assertNotIn("user1", run.errors)


class SyncThrottleTests(ProviderSyncApiTestCase):
    def test_the_sync_endpoint_has_its_own_tight_budget(self):
        """Tighter than any other admin scope, because one press is an
        authenticated multi-page request against a vendor that can
        suspend the account, a held worker, and the only call that can
        disable a slice of the inventory.

        The class attribute is patched rather than the setting, for the
        reason `test_the_probe_budget_actually_runs_out` documents:
        `SimpleRateThrottle.THROTTLE_RATES` is bound to the settings dict
        at import time, so `override_settings` cannot reach it.
        """
        from adminapi.throttling import AdminProviderSyncThrottle

        with mock.patch.object(
            AdminProviderSyncThrottle, "rate", "2/hour", create=True
        ):
            codes = [self.post().status_code for _ in range(3)]
        self.assertEqual(codes[:2], [status.HTTP_200_OK] * 2)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_a_throttled_call_contacts_no_provider(self):
        """The refusal must happen before the outbound request, or the
        limit bounds the response and not the work -- and the work here is
        an authenticated call against an account that can be suspended
        for making too many."""
        from adminapi.throttling import AdminProviderSyncThrottle

        with mock.patch.object(
            AdminProviderSyncThrottle, "rate", "1/hour", create=True
        ):
            self.post()
            adapter = FakeProviderSync([record()], provider="webshare")
            response = self.post(adapter=adapter)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(adapter.calls, [])

    def test_the_sync_scope_is_configured(self):
        """A throttle class whose scope has no rate silently allows
        everything."""
        from django.conf import settings as django_settings

        rates = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("admin_provider_sync", rates)
        self.assertTrue(rates["admin_provider_sync"])

    def test_it_does_not_consume_the_generic_admin_budget(self):
        """...and conversely, a dense dashboard polling the proxy list
        must not be able to exhaust the sync budget."""
        self.post()
        response = self.client.get("/api/admin/proxies/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SyncRunHistoryTests(ProviderSyncApiTestCase):
    def test_runs_are_listed(self):
        self.post({"dry_run": False})
        response = self.client.get(RUNS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["provider"], "webshare")
        self.assertEqual(row["created_count"], 1)
        self.assertEqual(row["triggered_by"], self.staff.email)

    def test_dry_runs_are_separable_from_real_ones(self):
        """A dashboard counting "endpoints disabled last week" that
        silently includes rehearsals reports work that never happened."""
        self.post()
        self.post({"dry_run": False})
        response = self.client.get(RUNS_URL, {"dry_run": "false"})
        self.assertEqual(response.data["count"], 1)
        self.assertFalse(response.data["results"][0]["dry_run"])

    def test_the_history_is_read_only(self):
        self.post({"dry_run": False})
        run = ProviderSyncRun.objects.get()
        self.assertEqual(
            self.client.patch(
                f"{RUNS_URL}{run.pk}/", {"created_count": 0}, format="json"
            ).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(f"{RUNS_URL}{run.pk}/").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_non_staff_cannot_read_the_history(self):
        self.client.credentials()
        self.authenticate(self.member)
        self.assertEqual(
            self.client.get(RUNS_URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_non_staff_cannot_trigger_a_sync(self):
        self.client.credentials()
        self.authenticate(self.member)
        response = self.client.post(SYNC_URL, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ProviderSyncRun.objects.exists())


class ProxySerializerTests(ProviderSyncApiTestCase):
    def test_the_proxy_list_exposes_the_sync_fields(self):
        self.post({"dry_run": False})
        response = self.client.get("/api/admin/proxies/", {"search": "45.38.107.97"})
        row = response.data["results"][0]
        self.assertEqual(row["external_id"], "1001")
        self.assertFalse(row["disabled_by_sync"])
        # Still no credentials in a list, ever.
        self.assertNotIn("password", row)

    def test_external_id_is_not_operator_writable(self):
        """A hand-typed external_id would make the next sync create a
        duplicate rather than match the row it was meant to name."""
        self.post({"dry_run": False})
        proxy = Proxy.objects.get(external_id="1001")
        response = self.client.patch(
            f"/api/admin/proxies/{proxy.pk}/",
            {"external_id": "9999"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        proxy.refresh_from_db()
        self.assertEqual(proxy.external_id, "1001")
