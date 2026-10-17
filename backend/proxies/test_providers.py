"""Provider sync (R9): the adapter, and the reconciliation rules.

**Nothing here touches the network.** The reconciler is driven through
`FakeProviderSync`; the Webshare adapter is driven through an injected
opener against captured response bodies, so pagination, header
construction and field mapping are all exercised for real while the socket
is the only thing stubbed. CI cannot reach Webshare and must not try.

The tests that matter most are the ones that assert a *refusal*:

* a rotation must not become delete-and-insert (`RotationTests`)
* a vanished endpoint must not be deleted (`VanishedTests`)
* a partial listing must not disable anything (`PartialListingTests`)
* a partial record must not blank a credential (`CredentialSafetyTests`)
* an operator's decision to disable must not be undone (`ReturningTests`)
* a vendor must not overwrite an independent measurement of its own
  network (`IntelPrecedenceTests`)
* the API token must not reach a log, an error or a stored row
  (`TokenHandlingTests`)

Each of those is a thing the naive implementation does by default.
"""

import json
import logging
import urllib.error
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from proxies.diversity import analyse_proxies
from proxies.intel import (
    INTEL_SKIPPED_FRESH_REGISTRY,
    INTEL_WRITTEN,
    IpFacts,
    is_provider_source,
    store_facts,
    store_provider_facts,
)
from proxies.models import (
    IpIntelRecord,
    IpIntelStatus,
    ProviderSyncRun,
    ProviderSyncStatus,
    Proxy,
    ProxyProbe,
    ProxyProtocol,
    ProxyTier,
)
from proxies.providers import (
    FakeProviderSync,
    ProviderCredentialsMissing,
    ProviderProxy,
    ProviderUnavailable,
    SyncAction,
    WebshareSync,
    _redact,
    sync_provider,
)

# A token-shaped string. Not a real one; long enough that `_redact` treats
# it as a secret rather than as a short string that might legitimately
# appear in prose.
FAKE_TOKEN = "0123456789abcdef0123456789abcdef01234567"


def record(
    external_id="1001",
    host="45.38.107.97",
    port=6014,
    username="user1",
    password="pass1",
    country="GB",
    city="London",
    asn=20738,
    asn_org="Heart Internet Limited",
    **kwargs,
):
    return ProviderProxy(
        external_id=external_id,
        host=host,
        port=port,
        protocol=ProxyProtocol.HTTP,
        username=username,
        password=password,
        country=country,
        city=city,
        asn=asn,
        asn_org=asn_org,
        **kwargs,
    )


def webshare_json(
    external_id=1001,
    host="45.38.107.97",
    port=6014,
    username="user1",
    password="pass1",
    country="GB",
    city="London",
    asn=20738,
    asn_org="Heart Internet Limited",
    valid=True,
    confident=True,
):
    """A `results[]` entry with exactly the fields a live call returns."""
    return {
        "id": external_id,
        "username": username,
        "password": password,
        "proxy_address": host,
        "port": port,
        "valid": valid,
        "last_verification": "2026-10-17T06:00:00.000000-07:00",
        "country_code": country,
        "city_name": city,
        "created_at": "2026-09-01T00:00:00.000000-07:00",
        "asn_name": asn_org,
        "asn_number": asn,
        "high_country_confidence": confident,
    }


def page(results, next_url=None, count=None):
    return {
        "count": len(results) if count is None else count,
        "next": next_url,
        "previous": None,
        "results": results,
    }


class _LogCollector(logging.Handler):
    def __init__(self):
        super().__init__(level=0)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    @property
    def text(self):
        return "\n".join(
            f"{record.name} {record.getMessage()} {record.args!r} "
            f"{getattr(record, 'audit', '')}"
            for record in self.records
        )


@contextmanager
def capture_all_logs():
    """Collect every record from every logger in the project tree.

    `assertLogs` is not enough here. `proxycrack` and `proxycrack.audit`
    are configured with `propagate=False`, so records raised under them
    never reach root and a root-level capture quietly passes by seeing
    nothing -- which is the worst possible way for a "the secret is not in
    the logs" assertion to succeed.
    """
    handler = _LogCollector()
    names = ["", "proxycrack", "proxycrack.audit", "proxycrack.providers"]
    loggers = [logging.getLogger(name) for name in names]
    previous = [(logger, logger.level) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        for logger, level in previous:
            logger.removeHandler(handler)
            logger.setLevel(level)


class FakeOpener:
    """Stands in for `urllib.request.urlopen`. Records what it was asked.

    Scripted by URL so a pagination test asserts the adapter followed
    `next` rather than guessing page numbers.
    """

    def __init__(self, pages=None, status=200, raises=None):
        self.pages = dict(pages or {})
        self.status = status
        self.raises = raises
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        if self.status != 200:
            return self.status, b"{}"
        body = self.pages.get(request.full_url)
        if body is None:
            # An unscripted URL is a bug in the adapter, not a 404 to
            # tolerate: it means it built a URL nobody expected.
            raise AssertionError(f"unscripted URL: {request.full_url}")
        return 200, json.dumps(body).encode("utf-8")


@override_settings(
    WEBSHARE_API_TOKEN=FAKE_TOKEN,
    WEBSHARE_API_URL="https://proxy.webshare.io/api/v2/proxy/list/",
    WEBSHARE_PAGE_SIZE=2,
)
class WebshareAdapterTests(TestCase):
    """The real adapter, with the socket replaced and nothing else."""

    def first_url(self, page_size=2):
        return (
            "https://proxy.webshare.io/api/v2/proxy/list/"
            f"?mode=direct&page=1&page_size={page_size}"
        )

    def test_maps_every_field_the_live_api_returns(self):
        opener = FakeOpener({self.first_url(): page([webshare_json()])})
        listing = WebshareSync(opener=opener).fetch()

        self.assertEqual(len(listing.records), 1)
        entry = listing.records[0]
        self.assertEqual(entry.external_id, "1001")
        self.assertEqual(entry.host, "45.38.107.97")
        self.assertEqual(entry.port, 6014)
        self.assertEqual(entry.username, "user1")
        self.assertEqual(entry.password, "pass1")
        self.assertEqual(entry.country, "GB")
        self.assertEqual(entry.city, "London")
        self.assertEqual(entry.asn, 20738)
        self.assertEqual(entry.asn_org, "Heart Internet Limited")
        self.assertTrue(listing.complete)

    def test_protocol_is_a_setting_not_a_guess(self):
        """The listing has no protocol field, so the adapter must not
        invent one per record."""
        opener = FakeOpener({self.first_url(): page([webshare_json()])})
        self.assertEqual(
            WebshareSync(opener=opener).fetch().records[0].protocol, "http"
        )
        with override_settings(WEBSHARE_PROTOCOL="socks5"):
            opener = FakeOpener({self.first_url(): page([webshare_json()])})
            self.assertEqual(
                WebshareSync(opener=opener).fetch().records[0].protocol, "socks5"
            )

    def test_sends_the_token_as_an_authorization_header(self):
        opener = FakeOpener({self.first_url(): page([webshare_json()])})
        WebshareSync(opener=opener).fetch()
        header = opener.requests[0].get_header("Authorization")
        self.assertEqual(header, f"Token {FAKE_TOKEN}")
        # ...and never in the URL, where it would land in a proxy log, a
        # browser history or an error message that quotes the request.
        self.assertNotIn(FAKE_TOKEN, opener.requests[0].full_url)

    def test_follows_next_until_exhausted(self):
        second = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=2&page_size=2"
        third = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=3&page_size=2"
        opener = FakeOpener(
            {
                self.first_url(): page(
                    [webshare_json(1), webshare_json(2, host="1.1.1.1")],
                    next_url=second,
                    count=5,
                ),
                second: page(
                    [webshare_json(3, host="2.2.2.2"), webshare_json(4, host="3.3.3.3")],
                    next_url=third,
                    count=5,
                ),
                third: page([webshare_json(5, host="4.4.4.4")], count=5),
            }
        )
        listing = WebshareSync(opener=opener).fetch()

        self.assertEqual(len(listing.records), 5)
        self.assertEqual(listing.pages, 3)
        self.assertEqual(listing.reported_total, 5)
        self.assertTrue(listing.complete)
        self.assertEqual(len(opener.requests), 3)

    def test_an_overall_cap_truncates_and_marks_the_listing_incomplete(self):
        second = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=2&page_size=2"
        opener = FakeOpener(
            {
                self.first_url(): page(
                    [webshare_json(1), webshare_json(2, host="1.1.1.1")],
                    next_url=second,
                    count=4,
                ),
                second: page(
                    [webshare_json(3, host="2.2.2.2"), webshare_json(4, host="3.3.3.3")],
                    count=4,
                ),
            }
        )
        listing = WebshareSync(opener=opener).fetch(max_records=3)

        self.assertEqual(len(listing.records), 3)
        self.assertTrue(listing.truncated)
        # The critical consequence: a truncated listing can never support
        # the "this endpoint vanished" conclusion.
        self.assertFalse(listing.complete)

    def test_page_size_reaches_the_url(self):
        opener = FakeOpener({self.first_url(7): page([webshare_json()])})
        WebshareSync(opener=opener).fetch(page_size=7)
        self.assertIn("page_size=7", opener.requests[0].full_url)

    def test_a_repeated_next_link_does_not_loop_forever(self):
        opener = FakeOpener(
            {self.first_url(): page([webshare_json()], next_url=self.first_url())}
        )
        listing = WebshareSync(opener=opener).fetch()
        self.assertFalse(listing.complete)
        self.assertEqual(len(opener.requests), 1)

    def test_a_malformed_record_is_skipped_not_fatal(self):
        opener = FakeOpener(
            {
                self.first_url(): page(
                    [
                        webshare_json(1),
                        {"id": 2, "port": 80},  # no proxy_address
                        webshare_json(3, host="2.2.2.2"),
                    ]
                )
            }
        )
        listing = WebshareSync(opener=opener).fetch()
        self.assertEqual(len(listing.records), 2)
        self.assertEqual(len(listing.errors), 1)
        self.assertFalse(listing.complete)

    def test_asn_tolerates_the_as_prefix(self):
        opener = FakeOpener({self.first_url(): page([webshare_json(asn="AS36352")])})
        self.assertEqual(WebshareSync(opener=opener).fetch().records[0].asn, 36352)

    def test_no_records_at_all_raises_rather_than_looking_like_an_empty_account(self):
        """An empty listing and a dead API are the same bytes to a
        reconciler, and they must not be the same conclusion."""
        opener = FakeOpener(status=500)
        with self.assertRaises(ProviderUnavailable):
            WebshareSync(opener=opener).fetch()


@override_settings(WEBSHARE_API_TOKEN="")
class MissingTokenTests(TestCase):
    def test_fetch_fails_loudly_and_names_the_variable(self):
        with self.assertRaises(ProviderCredentialsMissing) as ctx:
            WebshareSync(opener=FakeOpener()).fetch()
        self.assertIn("WEBSHARE_API_TOKEN", str(ctx.exception))

    def test_no_request_is_made(self):
        opener = FakeOpener()
        with self.assertRaises(ProviderCredentialsMissing):
            WebshareSync(opener=opener).fetch()
        self.assertEqual(opener.requests, [])

    def test_whitespace_is_not_a_token(self):
        with override_settings(WEBSHARE_API_TOKEN="   "):
            with self.assertRaises(ProviderCredentialsMissing):
                WebshareSync(opener=FakeOpener()).fetch()

    def test_sync_records_the_failed_run_rather_than_swallowing_it(self):
        """A schedule that fires and gets nowhere must leave evidence --
        otherwise it is indistinguishable from a schedule that is working
        and finding nothing to do."""
        with self.assertRaises(ProviderCredentialsMissing):
            sync_provider(WebshareSync(opener=FakeOpener()))
        run = ProviderSyncRun.objects.get()
        self.assertEqual(run.status, ProviderSyncStatus.FAILED)
        self.assertIn("WEBSHARE_API_TOKEN", run.errors)

    def test_the_management_command_exits_non_zero(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_webshare", "--yes", stdout=StringIO())
        self.assertIn("WEBSHARE_API_TOKEN", str(ctx.exception))


@override_settings(WEBSHARE_API_TOKEN=FAKE_TOKEN, WEBSHARE_PAGE_SIZE=2)
class TokenHandlingTests(TestCase):
    """The token is as sensitive as the credentials it buys.

    One GET with it returns every proxy username and password on the
    account, so it must not reach a log line, an exception message, a
    stored error row, or command output -- all four of which get pasted
    into tickets.
    """

    def first_url(self):
        return (
            "https://proxy.webshare.io/api/v2/proxy/list/"
            "?mode=direct&page=1&page_size=2"
        )

    def test_an_auth_failure_message_carries_no_token(self):
        opener = FakeOpener(status=401)
        with self.assertRaises(ProviderUnavailable) as ctx:
            WebshareSync(opener=opener).fetch()
        message = str(ctx.exception)
        self.assertNotIn(FAKE_TOKEN, message)
        # ...but it still says what to do about it.
        self.assertIn("WEBSHARE_API_TOKEN", message)

    def test_an_httperror_carrying_the_token_in_its_url_is_redacted(self):
        """The backstop. Nothing puts the token in a URL today; this
        proves that if something ever does, it still does not escape."""
        leaky = urllib.error.HTTPError(
            url=f"https://proxy.webshare.io/x?key={FAKE_TOKEN}",
            code=403,
            msg=f"forbidden for {FAKE_TOKEN}",
            hdrs=None,
            fp=None,
        )
        opener = FakeOpener(raises=leaky)
        with self.assertRaises(ProviderUnavailable) as ctx:
            WebshareSync(opener=opener).fetch()
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))

    def test_redact_leaves_short_strings_alone(self):
        """A deny-list that redacted any configured value would mangle
        ordinary prose when a setting happened to be `"http"`."""
        self.assertEqual(_redact("port is http", "http"), "port is http")
        self.assertIn("[redacted]", _redact(f"saw {FAKE_TOKEN}", FAKE_TOKEN))

    def test_nothing_logged_during_a_failing_sync_contains_the_token(self):
        opener = FakeOpener(status=403)
        with capture_all_logs() as captured:
            with self.assertRaises(ProviderUnavailable):
                sync_provider(WebshareSync(opener=opener))
        self.assertNotIn(FAKE_TOKEN, captured.text)

    def test_nothing_logged_during_a_successful_sync_contains_the_token(self):
        opener = FakeOpener({self.first_url(): page([webshare_json()])})
        with capture_all_logs() as captured:
            sync_provider(WebshareSync(opener=opener))
        self.assertNotIn(FAKE_TOKEN, captured.text)
        # The proxy passwords the listing carried must not be logged either.
        self.assertNotIn("pass1", captured.text)

    def test_the_stored_run_row_contains_no_token(self):
        opener = FakeOpener(status=403)
        with self.assertRaises(ProviderUnavailable):
            sync_provider(WebshareSync(opener=opener))
        run = ProviderSyncRun.objects.get()
        self.assertNotIn(FAKE_TOKEN, run.errors)
        self.assertEqual(run.status, ProviderSyncStatus.FAILED)

    def test_a_response_body_is_never_quoted_into_an_error(self):
        """The body carries the account's proxy passwords in plaintext.
        An error that quoted it would write the sellable asset into a log
        file -- which is exactly where it would later be found."""

        class BodyOpener(FakeOpener):
            def __call__(self, request, timeout):
                self.requests.append(request)
                return 200, b'not json, and it holds "pass1" and "user1"'

        opener = BodyOpener()
        with self.assertRaises(ProviderUnavailable) as ctx:
            WebshareSync(opener=opener).fetch()
        message = str(ctx.exception)
        self.assertNotIn("pass1", message)
        self.assertNotIn("user1", message)

    def test_command_output_contains_no_token(self):
        opener = FakeOpener({self.first_url(): page([webshare_json()])})
        out = StringIO()
        with override_settings(PROVIDER_SYNC_BACKEND="proxies.providers.WebshareSync"):
            from unittest import mock

            with mock.patch(
                "proxies.management.commands.sync_webshare.get_provider_sync",
                return_value=WebshareSync(opener=opener),
            ):
                call_command("sync_webshare", "--yes", "-v", "2", stdout=out)
        self.assertNotIn(FAKE_TOKEN, out.getvalue())
        # The passwords the listing carried must not be echoed either.
        self.assertNotIn("pass1", out.getvalue())


class SyncTestCase(TestCase):
    """Reconciliation, driven entirely by the fake."""

    provider = "webshare"

    def sync(self, records, **kwargs):
        pages = kwargs.pop("pages", None)
        fake_kwargs = {
            key: kwargs.pop(key)
            for key in ("complete", "truncated", "errors")
            if key in kwargs
        }
        adapter = FakeProviderSync(
            records, pages=pages, provider=self.provider, **fake_kwargs
        )
        return sync_provider(adapter, **kwargs)

    def make_proxy(self, **kwargs):
        defaults = {
            "protocol": ProxyProtocol.HTTP,
            "host": "45.38.107.97",
            "port": 6014,
            "tier": ProxyTier.PREMIUM,
        }
        defaults.update(kwargs)
        return Proxy.objects.create(**defaults)


class CreationTests(SyncTestCase):
    def test_creates_what_the_provider_lists(self):
        outcome = self.sync([record(), record("1002", host="64.137.96.74", port=6641)])

        self.assertEqual(outcome.count(SyncAction.CREATED), 2)
        self.assertEqual(Proxy.objects.count(), 2)
        proxy = Proxy.objects.get(external_id="1001")
        self.assertEqual(proxy.host, "45.38.107.97")
        self.assertEqual(proxy.provider, "webshare")
        self.assertEqual(proxy.username, "user1")
        self.assertEqual(proxy.password, "pass1")

    def test_closes_p22_country_and_city_are_filled(self):
        """P22: `Proxy.country` was free text and usually blank, so the
        mobile UI rendered `--`. The provider states both."""
        self.sync([record()])
        proxy = Proxy.objects.get(external_id="1001")
        self.assertEqual(proxy.country, "GB")
        self.assertEqual(proxy.city, "London")

    def test_sets_the_provider_label_r2_consumes(self):
        self.sync([record()])
        self.assertEqual(Proxy.objects.get().provider, "webshare")

    def test_created_rows_default_to_premium(self):
        """A mis-tagged import must never over-expose, which is the same
        rule the CSV importer follows."""
        self.sync([record()])
        self.assertEqual(Proxy.objects.get().tier, ProxyTier.PREMIUM)

    def test_a_dry_run_writes_nothing_but_still_reports(self):
        outcome = self.sync([record()], dry_run=True)
        self.assertEqual(outcome.count(SyncAction.CREATED), 1)
        self.assertEqual(Proxy.objects.count(), 0)
        run = ProviderSyncRun.objects.get()
        self.assertTrue(run.dry_run)
        self.assertEqual(run.created_count, 1)

    def test_the_run_row_records_the_tally(self):
        self.sync([record(), record("1002", host="1.2.3.4", port=99)])
        run = ProviderSyncRun.objects.get()
        self.assertEqual(run.provider, "webshare")
        self.assertEqual(run.source, "fake")
        self.assertEqual(run.fetched_count, 2)
        self.assertEqual(run.created_count, 2)
        self.assertEqual(run.status, ProviderSyncStatus.OK)
        self.assertIsNotNone(run.finished_at)


class IdempotencyTests(SyncTestCase):
    def test_running_twice_changes_nothing_the_second_time(self):
        records = [record(), record("1002", host="64.137.96.74", port=6641)]
        self.sync(records)

        before = {
            proxy.pk: proxy.updated_at for proxy in Proxy.objects.all()
        }
        outcome = self.sync(records)

        self.assertEqual(outcome.count(SyncAction.UNCHANGED), 2)
        self.assertEqual(outcome.count(SyncAction.CREATED), 0)
        self.assertEqual(outcome.count(SyncAction.UPDATED), 0)
        self.assertEqual(outcome.changed, 0)
        self.assertEqual(Proxy.objects.count(), 2)
        # Not merely "the values are the same" -- no write happened at
        # all. A no-op that still rewrites every row bumps `updated_at`
        # across the whole inventory every night and makes "what changed
        # last week" unanswerable.
        after = {proxy.pk: proxy.updated_at for proxy in Proxy.objects.all()}
        self.assertEqual(before, after)

    def test_a_third_run_is_also_a_no_op(self):
        records = [record()]
        self.sync(records)
        self.sync(records)
        outcome = self.sync(records)
        self.assertEqual(outcome.changed, 0)


class RotationTests(SyncTestCase):
    """The reason reconciliation is keyed on external_id.

    A provider rotating an address is one endpoint changing its IP.
    Matching on host:port would read it as one endpoint dying and an
    unrelated one being born -- which throws away the probe history of a
    proxy that never left and starts its replacement at the unprobed
    score.
    """

    def test_a_rotated_address_updates_in_place(self):
        self.sync([record()])
        proxy = Proxy.objects.get(external_id="1001")
        original_pk = proxy.pk

        outcome = self.sync([record(host="198.51.100.77", port=7000)])

        self.assertEqual(outcome.count(SyncAction.UPDATED), 1)
        self.assertEqual(outcome.count(SyncAction.CREATED), 0)
        self.assertEqual(outcome.count(SyncAction.DISABLED), 0)
        self.assertEqual(Proxy.objects.count(), 1)

        proxy.refresh_from_db()
        self.assertEqual(proxy.pk, original_pk)
        self.assertEqual(proxy.host, "198.51.100.77")
        self.assertEqual(proxy.port, 7000)

    def test_probe_history_survives_a_rotation(self):
        self.sync([record()])
        proxy = Proxy.objects.get(external_id="1001")
        for index in range(5):
            ProxyProbe.objects.create(
                proxy=proxy,
                succeeded=index % 2 == 0,
                latency_ms=100 + index,
                target="www.example.com:443",
            )
        self.assertEqual(proxy.probes.count(), 5)

        self.sync([record(host="198.51.100.77")])

        proxy.refresh_from_db()
        self.assertEqual(proxy.host, "198.51.100.77")
        self.assertEqual(proxy.probes.count(), 5)
        self.assertEqual(ProxyProbe.objects.count(), 5)

    def test_the_rotation_is_reported_as_such(self):
        self.sync([record()])
        outcome = self.sync([record(host="198.51.100.77")])
        item = outcome.of(SyncAction.UPDATED)[0]
        self.assertIn("host", item.fields)
        self.assertIn("rotated", item.detail)

    def test_two_endpoints_swapping_addresses_do_not_merge(self):
        """The nastiest rotation: the provider hands endpoint B the
        address endpoint A used to have. Keyed on host:port this silently
        merges two rentals into one."""
        self.sync(
            [
                record("1001", host="10.0.0.1", port=1000),
                record("1002", host="10.0.0.2", port=2000),
            ]
        )
        outcome = self.sync(
            [
                record("1001", host="10.0.0.9", port=1000),
                record("1002", host="10.0.0.1", port=2000),
            ]
        )
        self.assertEqual(outcome.count(SyncAction.UPDATED), 2)
        self.assertEqual(Proxy.objects.count(), 2)
        self.assertEqual(Proxy.objects.get(external_id="1001").host, "10.0.0.9")
        self.assertEqual(Proxy.objects.get(external_id="1002").host, "10.0.0.1")


class VanishedTests(SyncTestCase):
    def test_a_vanished_endpoint_is_disabled_not_deleted(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        gone = Proxy.objects.get(external_id="1002")

        outcome = self.sync([record("1001")])

        self.assertEqual(outcome.count(SyncAction.DISABLED), 1)
        gone.refresh_from_db()
        self.assertFalse(gone.is_enabled)
        self.assertTrue(gone.disabled_by_sync)
        self.assertEqual(Proxy.objects.count(), 2)

    def test_probe_history_survives_a_disappearance(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        gone = Proxy.objects.get(external_id="1002")
        ProxyProbe.objects.create(proxy=gone, succeeded=True, latency_ms=120)

        self.sync([record("1001")])

        self.assertEqual(ProxyProbe.objects.filter(proxy=gone).count(), 1)

    def test_a_second_run_does_not_re_disable(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        self.sync([record("1001")])
        outcome = self.sync([record("1001")])
        self.assertEqual(outcome.count(SyncAction.DISABLED), 0)
        self.assertEqual(outcome.count(SyncAction.UNCHANGED), 2)

    def test_another_providers_rows_are_never_touched(self):
        other = self.make_proxy(
            host="10.9.9.9", port=1234, provider="othervendor", external_id="x1"
        )
        manual = self.make_proxy(host="10.9.9.8", port=1234)

        self.sync([record("1001")])

        other.refresh_from_db()
        manual.refresh_from_db()
        self.assertTrue(other.is_enabled)
        self.assertTrue(manual.is_enabled)

    def test_a_dry_run_disables_nothing(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        outcome = self.sync([record("1001")], dry_run=True)
        self.assertEqual(outcome.count(SyncAction.DISABLED), 1)
        self.assertTrue(Proxy.objects.get(external_id="1002").is_enabled)


class ReturningTests(SyncTestCase):
    def test_a_returning_endpoint_is_re_enabled_not_duplicated(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        self.sync([record("1001")])
        gone = Proxy.objects.get(external_id="1002")
        self.assertFalse(gone.is_enabled)

        outcome = self.sync(
            [record("1001"), record("1002", host="10.0.0.2", port=2000)]
        )

        self.assertEqual(outcome.count(SyncAction.REENABLED), 1)
        self.assertEqual(outcome.count(SyncAction.CREATED), 0)
        self.assertEqual(Proxy.objects.count(), 2)
        gone.refresh_from_db()
        self.assertTrue(gone.is_enabled)
        self.assertFalse(gone.disabled_by_sync)

    def test_a_returning_endpoint_keeps_its_probe_history(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        gone = Proxy.objects.get(external_id="1002")
        ProxyProbe.objects.create(proxy=gone, succeeded=True, latency_ms=99)
        self.sync([record("1001")])
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        self.assertEqual(gone.probes.count(), 1)

    def test_an_operator_disabled_proxy_is_NOT_re_enabled(self):
        """The sync may only undo its own decisions.

        An operator who switches a proxy off has a reason -- it is leaking,
        it is being billed wrongly, it is reserved for a customer. A sync
        that silently switched it back on at 4am would revert a human
        decision with no record, which is the same class of bug as
        blanking a credential.
        """
        self.sync([record("1001")])
        proxy = Proxy.objects.get(external_id="1001")
        proxy.is_enabled = False
        proxy.save(update_fields=["is_enabled"])
        self.assertFalse(proxy.disabled_by_sync)

        outcome = self.sync([record("1001")])

        proxy.refresh_from_db()
        self.assertFalse(proxy.is_enabled)
        self.assertEqual(outcome.count(SyncAction.REENABLED), 0)
        self.assertIn("operator", outcome.outcomes[0].detail)

    def test_an_operator_disabled_proxy_still_gets_field_updates(self):
        """Leaving it disabled is not the same as ignoring it: a rotated
        address must still be recorded, or re-enabling it later would
        connect to somewhere it no longer is."""
        self.sync([record("1001")])
        proxy = Proxy.objects.get(external_id="1001")
        proxy.is_enabled = False
        proxy.save(update_fields=["is_enabled"])

        self.sync([record("1001", host="10.5.5.5")])

        proxy.refresh_from_db()
        self.assertEqual(proxy.host, "10.5.5.5")
        self.assertFalse(proxy.is_enabled)


class AdoptionTests(SyncTestCase):
    """The migration case: rows that pre-date this feature.

    Everything in the database before R9 was typed in by hand or imported
    from a CSV and carries no `external_id`, so the first sync would
    otherwise try to create a second row at the same host:port, hit
    `uniq_proxy_endpoint` and report a failure for an endpoint sitting
    right there with six probes of history.
    """

    def test_a_hand_added_proxy_is_adopted_not_duplicated(self):
        manual = self.make_proxy(
            host="45.38.107.97", port=6014, username="typed", password="typed"
        )
        ProxyProbe.objects.create(proxy=manual, succeeded=True, latency_ms=150)

        outcome = self.sync([record("1001")])

        self.assertEqual(outcome.count(SyncAction.ADOPTED), 1)
        self.assertEqual(outcome.count(SyncAction.CREATED), 0)
        self.assertEqual(Proxy.objects.count(), 1)

        manual.refresh_from_db()
        self.assertEqual(manual.external_id, "1001")
        self.assertEqual(manual.provider, "webshare")
        self.assertEqual(manual.probes.count(), 1)

    def test_adoption_fills_the_blank_metadata_p22_cares_about(self):
        manual = self.make_proxy(host="45.38.107.97", port=6014)
        self.assertEqual(manual.country, "")

        self.sync([record("1001")])

        manual.refresh_from_db()
        self.assertEqual(manual.country, "GB")
        self.assertEqual(manual.city, "London")

    def test_adoption_happens_once(self):
        self.make_proxy(host="45.38.107.97", port=6014)
        self.sync([record("1001")])
        outcome = self.sync([record("1001")])
        self.assertEqual(outcome.count(SyncAction.ADOPTED), 0)
        self.assertEqual(outcome.count(SyncAction.UNCHANGED), 1)

    def test_a_dry_run_reports_the_adoption_without_claiming_the_row(self):
        manual = self.make_proxy(host="45.38.107.97", port=6014)
        outcome = self.sync([record("1001")], dry_run=True)
        self.assertEqual(outcome.count(SyncAction.ADOPTED), 1)
        manual.refresh_from_db()
        self.assertEqual(manual.external_id, "")

    def test_a_live_rotation_onto_another_endpoints_address_resolves_itself(self):
        """Two endpoints both present, one moving onto the other's old
        address. The one that moved is processed by id, frees the address,
        and the newcomer lands on it -- no collision, no duplicate."""
        self.sync([record("1001", host="10.0.0.1", port=1000)])

        outcome = self.sync(
            [
                record("1001", host="10.0.0.5", port=5000),
                record("1002", host="10.0.0.1", port=1000),
            ]
        )

        self.assertEqual(outcome.count(SyncAction.FAILED), 0)
        self.assertEqual(Proxy.objects.count(), 2)
        self.assertEqual(Proxy.objects.get(external_id="1001").host, "10.0.0.5")
        self.assertEqual(Proxy.objects.get(external_id="1002").host, "10.0.0.1")

    def test_a_retired_endpoints_address_being_reissued_is_reported_not_resolved(self):
        """The case that genuinely cannot be resolved automatically.

        Endpoint 1001 is gone from the listing -- so its row keeps its
        address -- and the provider has reissued that address to a new
        endpoint 1002. Creating 1002 would collide with a row we are
        forbidden to delete (it holds probe history and may be referenced
        by a saved chain through `ChainHop.proxy`'s PROTECT). Rewriting
        1001's address would make its history a record of somewhere else.
        Both destroy evidence to tidy a tally, so it is reported and the
        operator decides.
        """
        self.sync([record("1001", host="10.0.0.1", port=1000)])
        kept = Proxy.objects.get(external_id="1001")
        ProxyProbe.objects.create(proxy=kept, succeeded=True, latency_ms=50)

        outcome = self.sync([record("1002", host="10.0.0.1", port=1000)])

        self.assertEqual(outcome.count(SyncAction.FAILED), 1)
        failed = outcome.of(SyncAction.FAILED)[0]
        self.assertEqual(failed.external_id, "1002")
        self.assertIn("1001", failed.detail)
        # Nothing was destroyed to make room.
        self.assertEqual(Proxy.objects.count(), 1)
        self.assertEqual(kept.probes.count(), 1)
        self.assertEqual(ProviderSyncRun.objects.latest("pk").failed_count, 1)
        # ...and the stale row is still correctly disabled as vanished.
        kept.refresh_from_db()
        self.assertFalse(kept.is_enabled)


class CredentialSafetyTests(SyncTestCase):
    """The bug `proxies.importers` already fixed once, applied here.

    The CSV rule is "an absent column is not data"; a present-but-empty
    cell is a deliberate clear. This module keeps the first half and drops
    the second: an empty string from an API at 4am is a truncated page or
    a vendor bug, not an instruction, and there is nobody watching.
    """

    def setUp(self):
        self.sync([record()])
        self.proxy = Proxy.objects.get(external_id="1001")
        self.assertEqual(self.proxy.username, "user1")

    def test_an_absent_credential_leaves_the_stored_one_alone(self):
        self.sync([record(username=None, password=None)])
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.username, "user1")
        self.assertEqual(self.proxy.password, "pass1")

    def test_an_empty_credential_leaves_the_stored_one_alone(self):
        self.sync([record(username="", password="")])
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.username, "user1")
        self.assertEqual(self.proxy.password, "pass1")

    def test_a_credential_only_response_still_updates_the_credential(self):
        """The rule must not become "never write credentials" -- a
        genuine rotation of the password has to land."""
        self.sync([record(password="rotated")])
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.password, "rotated")

    def test_a_partial_response_reports_unchanged_rather_than_updated(self):
        outcome = self.sync([record(username=None, password=None, city=None)])
        self.assertEqual(outcome.count(SyncAction.UNCHANGED), 1)

    def test_the_webshare_adapter_maps_a_missing_key_to_unstated(self):
        payload = webshare_json()
        del payload["password"]
        opener = FakeOpener(
            {
                "https://proxy.webshare.io/api/v2/proxy/list/"
                "?mode=direct&page=1&page_size=2": page([payload])
            }
        )
        with override_settings(WEBSHARE_API_TOKEN=FAKE_TOKEN, WEBSHARE_PAGE_SIZE=2):
            listing = WebshareSync(opener=opener).fetch()
        self.assertIsNone(listing.records[0].password)

    def test_the_webshare_adapter_maps_an_empty_value_to_unstated(self):
        opener = FakeOpener(
            {
                "https://proxy.webshare.io/api/v2/proxy/list/"
                "?mode=direct&page=1&page_size=2": page([webshare_json(password="")])
            }
        )
        with override_settings(WEBSHARE_API_TOKEN=FAKE_TOKEN, WEBSHARE_PAGE_SIZE=2):
            listing = WebshareSync(opener=opener).fetch()
        self.assertIsNone(listing.records[0].password)

    def test_a_malformed_record_changes_nothing(self):
        """A record the adapter could not parse never reaches the
        reconciler, so it cannot become a half-written update."""
        outcome = self.sync([], errors=["page 1 record 0: no proxy_address"])
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.username, "user1")
        self.assertEqual(outcome.status, ProviderSyncStatus.PARTIAL)


class PartialListingTests(SyncTestCase):
    """Half a listing looks exactly like half the inventory disappearing."""

    def setUp(self):
        self.sync(
            [
                record("1001", host="10.0.0.1", port=1000),
                record("1002", host="10.0.0.2", port=2000),
                record("1003", host="10.0.0.3", port=3000),
            ]
        )

    def test_an_incomplete_listing_disables_nothing(self):
        outcome = self.sync(
            [record("1001", host="10.0.0.1", port=1000)], complete=False
        )
        self.assertEqual(outcome.count(SyncAction.DISABLED), 0)
        self.assertEqual(Proxy.objects.filter(is_enabled=True).count(), 3)

    def test_an_incomplete_listing_is_reported_as_partial(self):
        outcome = self.sync(
            [record("1001", host="10.0.0.1", port=1000)], complete=False
        )
        self.assertEqual(outcome.status, ProviderSyncStatus.PARTIAL)
        self.assertEqual(
            ProviderSyncRun.objects.latest("pk").status, ProviderSyncStatus.PARTIAL
        )

    def test_the_absences_it_declined_to_judge_are_named_in_the_errors(self):
        outcome = self.sync(
            [record("1001", host="10.0.0.1", port=1000)], complete=False
        )
        self.assertTrue(
            any("were NOT disabled" in error for error in outcome.errors),
            outcome.errors,
        )

    def test_a_limit_truncating_the_listing_disables_nothing(self):
        outcome = self.sync(
            [
                record("1001", host="10.0.0.1", port=1000),
                record("1002", host="10.0.0.2", port=2000),
                record("1003", host="10.0.0.3", port=3000),
            ],
            limit=1,
        )
        self.assertEqual(outcome.count(SyncAction.DISABLED), 0)
        self.assertEqual(Proxy.objects.filter(is_enabled=True).count(), 3)

    def test_an_incomplete_listing_still_creates_new_endpoints(self):
        """The pages that did arrive are still worth acting on. Only the
        *absence* conclusion is unsafe."""
        outcome = self.sync(
            [
                record("1001", host="10.0.0.1", port=1000),
                record("1009", host="10.0.0.9", port=9000),
            ],
            complete=False,
        )
        self.assertEqual(outcome.count(SyncAction.CREATED), 1)
        self.assertTrue(Proxy.objects.filter(external_id="1009").exists())


class ProviderUnavailableTests(SyncTestCase):
    def test_an_unavailable_provider_changes_nothing(self):
        self.sync([record("1001"), record("1002", host="10.0.0.2", port=2000)])
        adapter = FakeProviderSync(unavailable="connection reset")
        with self.assertRaises(ProviderUnavailable):
            sync_provider(adapter)
        self.assertEqual(Proxy.objects.filter(is_enabled=True).count(), 2)

    def test_the_failed_run_is_recorded(self):
        with self.assertRaises(ProviderUnavailable):
            sync_provider(FakeProviderSync(unavailable="connection reset"))
        run = ProviderSyncRun.objects.latest("pk")
        self.assertEqual(run.status, ProviderSyncStatus.FAILED)


class DuplicateIdTests(SyncTestCase):
    def test_the_same_id_listed_twice_fails_the_second(self):
        outcome = self.sync([record("1001"), record("1001", host="10.0.0.9")])
        self.assertEqual(outcome.count(SyncAction.CREATED), 1)
        self.assertEqual(outcome.count(SyncAction.FAILED), 1)
        self.assertEqual(Proxy.objects.count(), 1)


class ProviderInvalidFlagTests(SyncTestCase):
    def test_the_providers_valid_flag_is_recorded_but_not_acted_on(self):
        """Webshare's `valid` answers the same question `probe_proxies`
        answers from our own measurements. Letting a vendor flag disable
        inventory would hand it a lever on what we can sell."""
        outcome = self.sync([record(valid=False)])
        proxy = Proxy.objects.get()
        self.assertTrue(proxy.is_enabled)
        self.assertTrue(proxy.is_alive)
        self.assertEqual(outcome.run.invalid_count, 1)


class ConstraintTests(SyncTestCase):
    def test_two_providers_may_share_an_external_id(self):
        self.make_proxy(
            host="10.1.1.1", port=1, provider="vendor-a", external_id="7"
        )
        self.make_proxy(
            host="10.1.1.2", port=1, provider="vendor-b", external_id="7"
        )
        self.assertEqual(Proxy.objects.filter(external_id="7").count(), 2)

    def test_one_provider_may_not_list_one_id_twice(self):
        self.make_proxy(
            host="10.1.1.1", port=1, provider="vendor-a", external_id="7"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_proxy(
                    host="10.1.1.2", port=1, provider="vendor-a", external_id="7"
                )

    def test_many_rows_may_have_a_blank_external_id(self):
        """Every hand-added and CSV row carries `""`. A plain unique index
        would have made them collide with each other."""
        self.make_proxy(host="10.1.1.1", port=1)
        self.make_proxy(host="10.1.1.2", port=1)
        self.assertEqual(Proxy.objects.filter(external_id="").count(), 2)


class IntelPrecedenceTests(SyncTestCase):
    """Verified beats asserted, and the row always says which it is."""

    def registry_record(self, host, *, asn=64500, age_days=0):
        now = timezone.now() - timedelta(days=age_days)
        return store_facts(
            IpFacts(
                query=host,
                ip=host,
                asn=asn,
                asn_org="Registry Org",
                prefix="45.38.104.0/22",
                country="GB",
                registry="ripencc",
                status=IpIntelStatus.OK,
                source="cymru-dns",
            ),
            now=now,
        )

    def test_a_sync_fills_intel_r2_could_not_otherwise_get(self):
        """The high-value integration: with no DNS resolver configured
        the Cymru lookup cannot run and every hop reports as unknown. The
        provider sends the answer unasked."""
        self.assertEqual(IpIntelRecord.objects.count(), 0)
        outcome = self.sync([record()])

        self.assertEqual(outcome.intel_written, 1)
        intel = IpIntelRecord.objects.get(query="45.38.107.97")
        self.assertEqual(intel.asn, 20738)
        self.assertEqual(intel.asn_org, "Heart Internet Limited")
        self.assertTrue(intel.is_known)

    def test_provider_rows_are_marked_as_provider_supplied(self):
        """An operator must be able to tell what a vendor asserted from
        what we verified: a vendor reselling one network under three
        names has an interest in its inventory looking diverse."""
        self.sync([record()])
        intel = IpIntelRecord.objects.get(query="45.38.107.97")
        self.assertEqual(intel.source, "provider:fake")
        self.assertTrue(is_provider_source(intel.source))

    def test_a_fresh_registry_record_is_not_overwritten(self):
        self.registry_record("45.38.107.97", asn=64500)
        outcome = self.sync([record(asn=20738)])

        self.assertEqual(outcome.intel_written, 0)
        self.assertEqual(outcome.intel_skipped, 1)
        intel = IpIntelRecord.objects.get(query="45.38.107.97")
        self.assertEqual(intel.asn, 64500)
        self.assertEqual(intel.source, "cymru-dns")

    def test_a_stale_registry_record_is_replaced(self):
        """A stale row is already invisible to the analysis, so the real
        choice is between `unknown` and a labelled vendor claim."""
        with override_settings(IP_INTEL_TTL_DAYS=1):
            self.registry_record("45.38.107.97", asn=64500, age_days=10)
        intel = IpIntelRecord.objects.get(query="45.38.107.97")
        self.assertFalse(intel.is_fresh())

        outcome = self.sync([record(asn=20738)])

        self.assertEqual(outcome.intel_written, 1)
        intel.refresh_from_db()
        self.assertEqual(intel.asn, 20738)
        self.assertEqual(intel.source, "provider:fake")

    def test_a_registry_lookup_always_supersedes_a_provider_claim(self):
        """The other direction of the rule, and it is unconditional."""
        self.sync([record(asn=20738)])
        self.registry_record("45.38.107.97", asn=64500)
        intel = IpIntelRecord.objects.get(query="45.38.107.97")
        self.assertEqual(intel.asn, 64500)
        self.assertEqual(intel.source, "cymru-dns")

    def test_a_provider_row_refreshes_its_own_earlier_claim(self):
        self.sync([record(asn=20738)])
        outcome = self.sync([record(asn=36352, asn_org="Hostpapa")])
        self.assertEqual(outcome.intel_written, 1)
        self.assertEqual(
            IpIntelRecord.objects.get(query="45.38.107.97").asn, 36352
        )

    def test_a_record_with_no_asn_writes_nothing(self):
        outcome = self.sync([record(asn=None, asn_org="")])
        self.assertEqual(outcome.intel_written, 0)
        self.assertEqual(outcome.intel_skipped, 1)
        self.assertEqual(IpIntelRecord.objects.count(), 0)

    def test_provider_rows_expire_sooner_than_registry_rows(self):
        """Shorter on purpose: it makes the assertion a stopgap that
        `refresh_ip_intel` later replaces with something nobody had an
        interest in."""
        with override_settings(
            IP_INTEL_TTL_DAYS=30, IP_INTEL_PROVIDER_TTL_DAYS=7
        ):
            self.sync([record()])
            provider_row = IpIntelRecord.objects.get(query="45.38.107.97")
            self.registry_record("10.0.0.1")
            registry_row = IpIntelRecord.objects.get(query="10.0.0.1")
        self.assertLess(provider_row.expires_at, registry_row.expires_at)

    def test_a_dry_run_writes_no_intel_but_previews_the_count(self):
        outcome = self.sync([record()], dry_run=True)
        self.assertEqual(outcome.intel_written, 1)
        self.assertEqual(IpIntelRecord.objects.count(), 0)

    def test_write_intel_can_be_turned_off(self):
        outcome = self.sync([record()], write_intel=False)
        self.assertEqual(outcome.intel_written, 0)
        self.assertEqual(IpIntelRecord.objects.count(), 0)

    def test_store_provider_facts_returns_why_it_declined(self):
        self.registry_record("1.2.3.4")
        facts = IpFacts(
            query="1.2.3.4", ip="1.2.3.4", asn=99, source="provider:fake"
        )
        _row, outcome = store_provider_facts(facts)
        self.assertEqual(outcome, INTEL_SKIPPED_FRESH_REGISTRY)

        _row, outcome = store_provider_facts(
            IpFacts(query="9.9.9.9", ip="9.9.9.9", asn=99, source="provider:fake")
        )
        self.assertEqual(outcome, INTEL_WRITTEN)


class R2IntegrationTests(SyncTestCase):
    """The two features meeting, on the shape of the real account.

    Every endpoint on a single-vendor account shares one provider, and
    some share an ASN. A chain built from that pool is one operator
    watching both ends -- which is the single most important weakness R2
    exists to name, and which it can only name because the sync wrote
    `Proxy.provider` and `IpIntelRecord`.
    """

    #: Two real endpoints from the account, both on AS36352 (Hostpapa).
    SHARED_ASN = [
        record("1003", host="198.23.243.226", port=6361, country="US",
               city="Los Angeles", asn=36352, asn_org="Hostpapa"),
        record("1007", host="198.46.161.42", port=5092, country="US",
               city="Los Angeles", asn=36352, asn_org="Hostpapa"),
    ]
    MIDDLE = record(
        "1008", host="64.137.10.153", port=5803, country="DE",
        city="Frankfurt", asn=212238, asn_org="Datacamp Limited",
    )

    def test_a_chain_from_one_providers_pool_raises_shared_provider(self):
        self.sync([self.SHARED_ASN[0], self.MIDDLE, self.SHARED_ASN[1]])
        chain = [
            Proxy.objects.get(external_id="1003"),
            Proxy.objects.get(external_id="1008"),
            Proxy.objects.get(external_id="1007"),
        ]

        report = analyse_proxies(chain)
        codes = [finding.code for finding in report.findings]

        self.assertIn("entry_exit_shared_provider", codes)

    def test_the_shared_asn_is_detected_from_provider_supplied_intel(self):
        self.sync([self.SHARED_ASN[0], self.MIDDLE, self.SHARED_ASN[1]])
        chain = [
            Proxy.objects.get(external_id="1003"),
            Proxy.objects.get(external_id="1008"),
            Proxy.objects.get(external_id="1007"),
        ]
        report = analyse_proxies(chain)
        codes = [finding.code for finding in report.findings]

        self.assertIn("entry_exit_shared_asn", codes)
        # And the hops are no longer `unknown`, which is what they were
        # before the sync existed on a host with no resolver.
        self.assertEqual(report.coverage, 1.0)

    def test_before_the_sync_the_same_chain_is_entirely_unknown(self):
        """The baseline this feature moved. Without provider intel and
        without a resolver, every hop reports unknown and the coverage
        ceiling holds the score at 50 (D21)."""
        proxies = [
            Proxy.objects.create(
                protocol=ProxyProtocol.HTTP,
                host=entry.host,
                port=entry.port,
                tier=ProxyTier.PREMIUM,
            )
            for entry in (self.SHARED_ASN[0], self.MIDDLE, self.SHARED_ASN[1])
        ]
        report = analyse_proxies(proxies)
        self.assertEqual(report.coverage, 0.0)
        self.assertLessEqual(report.score, 50.0)


class ManagementCommandTests(SyncTestCase):
    def run_command(self, *args, records=None, **kwargs):
        from unittest import mock

        adapter = FakeProviderSync(
            records if records is not None else [record()], provider="webshare"
        )
        out = StringIO()
        with mock.patch(
            "proxies.management.commands.sync_webshare.get_provider_sync",
            return_value=adapter,
        ):
            call_command("sync_webshare", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_it_defaults_to_a_dry_run(self):
        output = self.run_command()
        self.assertIn("DRY RUN", output)
        self.assertEqual(Proxy.objects.count(), 0)

    def test_yes_commits(self):
        self.run_command("--yes")
        self.assertEqual(Proxy.objects.count(), 1)

    def test_dry_run_and_yes_contradict(self):
        with self.assertRaises(CommandError):
            self.run_command("--dry-run", "--yes")

    def test_verbose_output_names_each_endpoint(self):
        output = self.run_command("--yes", "-v", "2")
        self.assertIn("45.38.107.97:6014", output)
        self.assertIn("created", output)

    def test_output_never_carries_a_password(self):
        output = self.run_command("--yes", "-v", "2")
        self.assertNotIn("pass1", output)
        self.assertNotIn("user1", output)

    def test_a_partial_listing_is_called_out_loudly(self):
        from unittest import mock

        adapter = FakeProviderSync([record()], provider="webshare", complete=False)
        out = StringIO()
        with mock.patch(
            "proxies.management.commands.sync_webshare.get_provider_sync",
            return_value=adapter,
        ):
            call_command("sync_webshare", "--yes", stdout=out)
        self.assertIn("PARTIAL LISTING", out.getvalue())

    def test_limit_is_passed_through(self):
        from unittest import mock

        adapter = FakeProviderSync(
            [record("1001"), record("1002", host="10.0.0.2", port=2)],
            provider="webshare",
        )
        with mock.patch(
            "proxies.management.commands.sync_webshare.get_provider_sync",
            return_value=adapter,
        ):
            call_command("sync_webshare", "--yes", "--limit", "1", stdout=StringIO())
        self.assertEqual(adapter.calls[0]["max_records"], 1)
        self.assertEqual(Proxy.objects.count(), 1)

    def test_no_intel_skips_the_cache_fill(self):
        self.run_command("--yes", "--no-intel")
        self.assertEqual(IpIntelRecord.objects.count(), 0)
