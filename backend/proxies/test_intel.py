"""IP intelligence: the source abstraction, the cache, and the parsers.

**No test here opens a socket.** `FakeIpIntel` answers every lookup; the
wire-format parsers are exercised against hand-built byte strings; the
rate limiter runs on an injected clock so bounding it does not cost the
suite a real minute.

That last one matters more than it looks. A rate limiter that is only
testable by waiting is a rate limiter nobody tests, and an untested one
against a free third-party service is how a project gets blocked.
"""

import struct
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from proxies.checks import (
    check_ip_intel_has_a_resolver,
    check_ip_intel_settings,
)
from proxies.intel import (
    CymruDnsIpIntel,
    DnsError,
    DnsNameError,
    FakeIpIntel,
    IpApiIntel,
    IpFacts,
    IpIntelUnavailable,
    RateLimiter,
    _dns_txt,
    _encode_name,
    _parse_txt,
    cached_facts,
    configured_resolvers,
    get_ip_intel,
    intel_ttl,
    lookup_hosts,
    normalise_host,
    parse_ip,
    store_facts,
)
from proxies.models import (
    IpIntelRecord,
    IpIntelStatus,
    Proxy,
    ProxyProtocol,
    ProxyTier,
)


def facts(host, asn=64500, org="Acme", prefix="198.51.100.0/24", country="NL"):
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


class ScriptedIntel(FakeIpIntel):
    """A `FakeIpIntel` reachable through `IP_INTEL_BACKEND`.

    `get_ip_intel()` instantiates with no arguments, exactly as
    `get_prober()` does, so a test that needs the *settings* path rather
    than dependency injection sets the class-level script and points the
    setting here.
    """

    SCRIPT = {}
    INSTANCES = []

    def __init__(self):
        super().__init__(results=dict(ScriptedIntel.SCRIPT))
        ScriptedIntel.INSTANCES.append(self)


# ---------------------------------------------------------------------------
# Host handling
# ---------------------------------------------------------------------------


class HostNormalisationTests(TestCase):
    def test_case_trailing_dot_and_brackets_collapse_to_one_key(self):
        self.assertEqual(normalise_host(" Proxy.Example. "), "proxy.example")
        self.assertEqual(normalise_host("[2001:db8::1]"), "2001:db8::1")
        self.assertEqual(normalise_host(""), "")
        self.assertEqual(normalise_host(None), "")

    def test_literal_addresses_are_recognised(self):
        self.assertIsNotNone(parse_ip("198.51.100.4"))
        self.assertIsNotNone(parse_ip("2001:db8::1"))
        self.assertIsNone(parse_ip("proxy.example"))


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


class FakeIpIntelTests(TestCase):
    def test_unscripted_hosts_are_unknown_rather_than_invented(self):
        """The property the whole test suite leans on: a fake that made up
        a plausible ASN would let a test assert diversity the production
        code never computed."""
        result = FakeIpIntel().lookup("198.51.100.4")
        self.assertFalse(result.is_known)
        self.assertEqual(result.status, IpIntelStatus.NOT_FOUND)

    def test_scripted_sequence_is_consumed_in_order_and_the_last_repeats(self):
        intel = FakeIpIntel(
            {"a": [facts("a", asn=1), facts("a", asn=2)]}
        )
        self.assertEqual(intel.lookup("a").asn, 1)
        self.assertEqual(intel.lookup("a").asn, 2)
        self.assertEqual(intel.lookup("a").asn, 2)

    def test_the_key_is_authoritative_over_a_mismatched_query_field(self):
        intel = FakeIpIntel({"a": facts("totally-different")})
        self.assertEqual(intel.lookup("a").query, "a")

    def test_calls_are_recorded_in_order(self):
        intel = FakeIpIntel()
        intel.lookup("b")
        intel.lookup("A")
        self.assertEqual(intel.calls, ["b", "a"])

    def test_can_be_scripted_unavailable(self):
        with self.assertRaises(IpIntelUnavailable):
            FakeIpIntel(unavailable=True).lookup("a")

    def test_it_never_sleeps_for_the_rate_limiter(self):
        self.assertFalse(FakeIpIntel.rate_limited)

    @override_settings(IP_INTEL_BACKEND="proxies.test_intel.ScriptedIntel")
    def test_get_ip_intel_honours_the_setting(self):
        self.assertIsInstance(get_ip_intel(), ScriptedIntel)

    def test_the_default_source_is_the_real_one(self):
        self.assertIsInstance(get_ip_intel(), CymruDnsIpIntel)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class CacheTtlTests(TestCase):
    def test_store_facts_writes_looked_up_at_and_an_expiry(self):
        now = timezone.now()
        record = store_facts(facts("198.51.100.4"), now=now)
        self.assertEqual(record.looked_up_at, now)
        self.assertEqual(record.expires_at, now + timedelta(days=30))
        self.assertTrue(record.is_fresh(now))

    @override_settings(IP_INTEL_TTL_DAYS=2, IP_INTEL_NEGATIVE_TTL_HOURS=1)
    def test_ttl_is_configurable_and_failures_expire_sooner(self):
        self.assertEqual(intel_ttl(IpIntelStatus.OK), timedelta(days=2))
        self.assertEqual(intel_ttl(IpIntelStatus.ERROR), timedelta(hours=1))
        self.assertEqual(intel_ttl(IpIntelStatus.NOT_FOUND), timedelta(hours=1))

    def test_a_stale_record_is_not_returned_by_the_read_path(self):
        now = timezone.now()
        store_facts(facts("198.51.100.4"), now=now - timedelta(days=31))

        self.assertEqual(cached_facts(["198.51.100.4"], now=now), {})
        # ...but it is still there, and the refresh path can see it.
        self.assertIn(
            "198.51.100.4",
            cached_facts(["198.51.100.4"], now=now, include_stale=True),
        )

    def test_lookup_hosts_uses_the_cache_until_the_ttl_expires(self):
        """The TTL test the spec asks for, asserted on `calls` rather than
        on a timestamp: proving the source was not re-queried is the
        actual requirement."""
        intel = FakeIpIntel({"198.51.100.4": facts("198.51.100.4")})
        start = timezone.now()

        first = lookup_hosts(["198.51.100.4"], intel=intel, now=start)
        self.assertEqual(intel.calls, ["198.51.100.4"])
        self.assertEqual(first.looked_up, ["198.51.100.4"])
        self.assertEqual(first.from_cache, [])

        # Inside the TTL: no second lookup.
        second = lookup_hosts(
            ["198.51.100.4"], intel=intel, now=start + timedelta(days=29)
        )
        self.assertEqual(intel.calls, ["198.51.100.4"])
        self.assertEqual(second.from_cache, ["198.51.100.4"])
        self.assertEqual(second.looked_up, [])

        # Past the TTL: queried again.
        third = lookup_hosts(
            ["198.51.100.4"], intel=intel, now=start + timedelta(days=31)
        )
        self.assertEqual(intel.calls, ["198.51.100.4", "198.51.100.4"])
        self.assertEqual(third.looked_up, ["198.51.100.4"])
        self.assertEqual(IpIntelRecord.objects.count(), 1)  # replaced, not appended

    def test_force_ignores_a_fresh_record(self):
        intel = FakeIpIntel({"198.51.100.4": facts("198.51.100.4")})
        lookup_hosts(["198.51.100.4"], intel=intel)
        lookup_hosts(["198.51.100.4"], intel=intel, force=True)
        self.assertEqual(len(intel.calls), 2)

    def test_a_failed_lookup_is_cached_on_the_short_ttl(self):
        now = timezone.now()
        intel = FakeIpIntel({"198.51.100.4": IpFacts.unknown("198.51.100.4", detail="boom")})

        batch = lookup_hosts(["198.51.100.4"], intel=intel, now=now)

        record = batch.records["198.51.100.4"]
        self.assertEqual(record.status, IpIntelStatus.ERROR)
        self.assertEqual(record.detail, "boom")
        self.assertEqual(record.expires_at, now + timedelta(hours=6))
        self.assertEqual(batch.failed, ["198.51.100.4"])
        self.assertEqual(batch.known, 0)

    def test_duplicate_and_blank_hosts_collapse_to_one_lookup(self):
        intel = FakeIpIntel({"198.51.100.4": facts("198.51.100.4")})
        lookup_hosts(["198.51.100.4", "198.51.100.4", "", None], intel=intel)
        self.assertEqual(intel.calls, ["198.51.100.4"])

    def test_an_unavailable_source_stops_the_run_instead_of_blaming_the_hosts(self):
        """A missing resolver is not a fact about ten thousand addresses,
        and must not be written as one."""
        intel = FakeIpIntel(unavailable=True)

        batch = lookup_hosts(["a", "b", "c"], intel=intel)

        self.assertTrue(batch.unavailable)
        self.assertEqual(batch.looked_up, [])
        self.assertEqual(IpIntelRecord.objects.count(), 0)
        self.assertEqual(intel.calls, ["a"])  # stopped after the first

    def test_a_source_that_raises_is_our_bug_not_the_hosts_fault(self):
        class Exploding(FakeIpIntel):
            def lookup(self, host, *, timeout=5.0):
                raise RuntimeError("typo in the parser")

        with self.assertLogs("proxycrack.intel", level="ERROR"):
            batch = lookup_hosts(["198.51.100.4"], intel=Exploding())

        record = batch.records["198.51.100.4"]
        self.assertEqual(record.status, IpIntelStatus.ERROR)
        self.assertIn("RuntimeError", record.detail)

    def test_is_known_distinguishes_a_row_from_an_answer(self):
        record = store_facts(
            IpFacts.unknown("a", status=IpIntelStatus.NOT_FOUND), now=timezone.now()
        )
        self.assertTrue(record.is_fresh())
        self.assertFalse(record.is_known)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiterTests(TestCase):
    def setUp(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleeper(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def limiter(self, per_minute):
        return RateLimiter(per_minute, clock=self.clock, sleeper=self.sleeper)

    def test_the_first_call_is_free_and_the_next_waits(self):
        limiter = self.limiter(60)  # one per second
        self.assertEqual(limiter.wait(), 0.0)
        self.assertEqual(limiter.wait(), 1.0)
        self.assertEqual(limiter.wait(), 1.0)
        self.assertEqual(self.slept, [1.0, 1.0])

    def test_time_already_spent_counts_towards_the_interval(self):
        """A slow lookup is not also charged a full wait afterwards."""
        limiter = self.limiter(60)
        limiter.wait()
        self.now += 5.0  # the lookup itself took five seconds
        self.assertEqual(limiter.wait(), 0.0)
        self.assertEqual(self.slept, [])

    def test_zero_disables_it(self):
        limiter = self.limiter(0)
        for _ in range(100):
            self.assertEqual(limiter.wait(), 0.0)
        self.assertEqual(self.slept, [])

    @override_settings(IP_INTEL_RATE_LIMIT_PER_MINUTE=0)
    def test_lookup_hosts_does_not_sleep_when_the_limit_is_off(self):
        intel = FakeIpIntel()
        with mock.patch("proxies.intel.time.sleep") as sleep:
            lookup_hosts(["a", "b", "c"], intel=intel)
        sleep.assert_not_called()

    def test_the_fake_is_never_rate_limited_even_when_the_setting_is_on(self):
        limiter = self.limiter(60)
        lookup_hosts(["a", "b", "c"], intel=FakeIpIntel(), limiter=limiter)
        self.assertEqual(self.slept, [])


# ---------------------------------------------------------------------------
# DNS wire format
# ---------------------------------------------------------------------------


def dns_response(txt_strings, *, rcode=0, flags=0x8180, qname="x.origin.asn.cymru.com"):
    """A DNS response packet with one question and N TXT answers."""
    question = _encode_name(qname) + struct.pack(">HH", 16, 1)
    header = struct.pack(">HHHHHH", 0x1234, flags | rcode, 1, len(txt_strings), 0, 0)
    body = b""
    for text in txt_strings:
        encoded = text.encode("ascii")
        rdata = bytes([len(encoded)]) + encoded
        body += b"\xc0\x0c" + struct.pack(">HHIH", 16, 1, 300, len(rdata)) + rdata
    return header + question + body


class DnsParsingTests(TestCase):
    def test_a_txt_answer_round_trips(self):
        packet = dns_response(["15169 | 8.8.8.0/24 | US | arin | 2007-03-13"])
        self.assertEqual(
            _parse_txt(packet),
            ["15169 | 8.8.8.0/24 | US | arin | 2007-03-13"],
        )

    def test_several_answers_all_come_back(self):
        packet = dns_response(["a | 1", "b | 2"])
        self.assertEqual(_parse_txt(packet), ["a | 1", "b | 2"])

    def test_nxdomain_is_its_own_error(self):
        """For an origin lookup NXDOMAIN is an answer -- nobody announces a
        route covering this address -- so it must not look like a failure."""
        with self.assertRaises(DnsNameError):
            _parse_txt(dns_response([], rcode=3))

    def test_a_server_failure_is_an_error(self):
        with self.assertRaises(DnsError):
            _parse_txt(dns_response([], rcode=2))

    def test_a_truncated_response_is_refused_rather_than_half_used(self):
        """A partial answer would under-report shared networks, which is
        the one direction this feature must never fail in."""
        with self.assertRaises(DnsError):
            _parse_txt(dns_response(["a | 1"], flags=0x8380))

    def test_a_short_packet_is_an_error(self):
        with self.assertRaises(DnsError):
            _parse_txt(b"\x00\x01")

    def test_name_encoding_rejects_an_oversized_label(self):
        with self.assertRaises(DnsError):
            _encode_name("x" * 64 + ".example")

    def test_no_resolver_configured_is_unavailable_not_a_lookup_failure(self):
        with self.assertRaises(IpIntelUnavailable) as caught:
            _dns_txt("x.example", [], 1.0)
        self.assertIn("IP_INTEL_DNS_RESOLVERS", str(caught.exception))

    @override_settings(IP_INTEL_DNS_RESOLVERS=["192.0.2.53", " "])
    def test_configured_resolvers_wins_over_the_host_file(self):
        self.assertEqual(configured_resolvers(), ["192.0.2.53"])


class CymruParsingTests(TestCase):
    def test_origin_record_fields(self):
        parsed = CymruDnsIpIntel.parse_origin(
            ["15169 | 8.8.8.0/24 | US | arin | 2007-03-13"]
        )
        self.assertEqual(parsed, (15169, "8.8.8.0/24", "US", "arin"))

    def test_the_most_specific_prefix_wins(self):
        """An address covered by two announcements gets two records. The
        longer prefix is the route traffic follows and the tighter
        correlation claim."""
        parsed = CymruDnsIpIntel.parse_origin(
            [
                "100 | 198.51.0.0/16 | NL | ripencc | 2000-01-01",
                "200 | 198.51.100.0/24 | NL | ripencc | 2000-01-01",
            ]
        )
        self.assertEqual(parsed[0], 200)
        self.assertEqual(parsed[1], "198.51.100.0/24")

    def test_a_multi_origin_prefix_takes_the_first_asn(self):
        parsed = CymruDnsIpIntel.parse_origin(
            ["13335 209242 | 1.1.1.0/24 | US | arin | 2010-01-01"]
        )
        self.assertEqual(parsed[0], 13335)

    def test_unparseable_records_yield_nothing_rather_than_a_guess(self):
        self.assertIsNone(CymruDnsIpIntel.parse_origin([]))
        self.assertIsNone(CymruDnsIpIntel.parse_origin(["not a record"]))
        self.assertIsNone(CymruDnsIpIntel.parse_origin(["abc | 1.2.3.0/24"]))

    def test_query_names_are_reversed_per_the_zone_convention(self):
        intel = CymruDnsIpIntel()
        self.assertEqual(
            intel.query_name("1.2.3.4"), "4.3.2.1.origin.asn.cymru.com"
        )
        self.assertTrue(
            intel.query_name("2001:db8::1").endswith(".origin6.asn.cymru.com")
        )

    def test_a_missing_resolver_surfaces_as_unavailable_not_as_unknown(self):
        """`IpIntelUnavailable` has to escape `lookup`: it means the source
        cannot be used at all, and recording it per host would write ten
        thousand identical error rows."""
        with self.assertRaises(IpIntelUnavailable):
            CymruDnsIpIntel(resolvers=[]).lookup("198.51.100.4")


class IpApiParsingTests(TestCase):
    def test_a_successful_payload(self):
        parsed = IpApiIntel.parse(
            "198.51.100.4",
            "198.51.100.4",
            {
                "status": "success",
                "countryCode": "nl",
                "as": "AS64500 Acme BV",
                "asname": "ACME",
            },
        )
        self.assertEqual(parsed.asn, 64500)
        self.assertEqual(parsed.asn_org, "ACME")
        self.assertEqual(parsed.country, "NL")

    def test_it_reports_no_announced_prefix(self):
        """The honest consequence of using this source.

        `proxies.diversity` still derives a /24 neighbourhood from the
        address, so same-rack detection survives -- but the case the
        announced prefix exists for does not: two hops inside one
        operator's /19 in different /24s will read as unrelated.
        """
        parsed = IpApiIntel.parse(
            "198.51.100.4",
            "198.51.100.4",
            {"status": "success", "as": "AS64500 Acme", "countryCode": "NL"},
        )
        self.assertEqual(parsed.prefix, "")
        self.assertEqual(parsed.registry, "")

    def test_a_failure_payload_is_not_found(self):
        parsed = IpApiIntel.parse(
            "10.0.0.1", "10.0.0.1", {"status": "fail", "message": "private range"}
        )
        self.assertEqual(parsed.status, IpIntelStatus.NOT_FOUND)
        self.assertIn("private range", parsed.detail)

    def test_a_payload_with_no_as_field(self):
        parsed = IpApiIntel.parse("a", "1.2.3.4", {"status": "success"})
        self.assertFalse(parsed.is_known)


# ---------------------------------------------------------------------------
# Settings checks
# ---------------------------------------------------------------------------


class IntelCheckTests(TestCase):
    def ids(self, warnings):
        return [warning.id for warning in warnings]

    def test_defaults_are_clean(self):
        self.assertEqual(check_ip_intel_settings(None), [])

    @override_settings(IP_INTEL_TTL_DAYS=0)
    def test_a_zero_ttl_is_flagged(self):
        self.assertIn("proxies.W006", self.ids(check_ip_intel_settings(None)))

    @override_settings(IP_INTEL_TTL_DAYS=1, IP_INTEL_NEGATIVE_TTL_HOURS=48)
    def test_a_negative_ttl_longer_than_the_positive_one_is_flagged(self):
        self.assertIn("proxies.W007", self.ids(check_ip_intel_settings(None)))

    @override_settings(IP_INTEL_DNS_RESOLVERS=[])
    def test_the_resolver_warning_is_deploy_only(self):
        """It fires on a host with no resolver, which on a Windows dev box
        is permanent -- hence `deploy=True`, so it is not a warning every
        developer learns to scroll past."""
        with mock.patch("proxies.intel.configured_resolvers", return_value=[]):
            self.assertIn(
                "proxies.W008", self.ids(check_ip_intel_has_a_resolver(None))
            )

    @override_settings(IP_INTEL_BACKEND="proxies.intel.IpApiIntel")
    def test_the_resolver_warning_does_not_apply_to_the_http_source(self):
        with mock.patch("proxies.intel.configured_resolvers", return_value=[]):
            self.assertEqual(check_ip_intel_has_a_resolver(None), [])


# ---------------------------------------------------------------------------
# refresh_ip_intel
# ---------------------------------------------------------------------------


class RefreshIpIntelCommandTests(TestCase):
    def setUp(self):
        self.nl = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.4",
            port=1080,
            country="NL",
            tier=ProxyTier.PREMIUM,
            provider="Acme",
        )
        self.de = Proxy.objects.create(
            protocol=ProxyProtocol.HTTP,
            host="203.0.113.9",
            port=8080,
            country="DE",
            tier=ProxyTier.FREE,
        )
        self.disabled = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="192.0.2.7",
            port=1080,
            is_enabled=False,
        )
        self.intel = FakeIpIntel(
            {
                "198.51.100.4": facts("198.51.100.4", asn=64500),
                "203.0.113.9": facts("203.0.113.9", asn=64501, prefix="203.0.113.0/24"),
                "192.0.2.7": facts("192.0.2.7", asn=64502, prefix="192.0.2.0/24"),
            }
        )

    def call(self, *args, intel=None, **options):
        out = StringIO()
        options.setdefault("stdout", out)
        options.setdefault("stderr", StringIO())
        with mock.patch(
            "proxies.management.commands.refresh_ip_intel.get_ip_intel",
            return_value=intel or self.intel,
        ):
            call_command("refresh_ip_intel", *args, **options)
        return out.getvalue()

    def test_it_looks_up_every_enabled_proxy_host(self):
        output = self.call()
        self.assertEqual(sorted(self.intel.calls), ["198.51.100.4", "203.0.113.9"])
        self.assertEqual(IpIntelRecord.objects.count(), 2)
        self.assertIn("2 looked up", output)

    def test_disabled_proxies_are_skipped_unless_asked_for(self):
        self.call("--include-disabled")
        self.assertIn("192.0.2.7", self.intel.calls)

    def test_the_unit_of_work_is_the_host_not_the_proxy(self):
        """A vendor selling many ports behind one hostname is one lookup."""
        for port in (1081, 1082, 1083):
            Proxy.objects.create(
                protocol=ProxyProtocol.SOCKS5, host="198.51.100.4", port=port
            )
        self.call()
        self.assertEqual(self.intel.calls.count("198.51.100.4"), 1)

    def test_fresh_records_are_not_re_queried(self):
        self.call()
        self.intel.calls.clear()
        output = self.call()
        self.assertEqual(self.intel.calls, [])
        self.assertIn("2 already fresh", output)

    def test_force_re_queries(self):
        self.call()
        self.intel.calls.clear()
        self.call("--force")
        self.assertEqual(len(self.intel.calls), 2)

    def test_dry_run_writes_nothing(self):
        output = self.call("--dry-run")
        self.assertEqual(IpIntelRecord.objects.count(), 0)
        self.assertEqual(self.intel.calls, [])
        self.assertIn("would look up", output)

    def test_limit_bounds_the_run(self):
        self.call("--limit", "1")
        self.assertEqual(len(self.intel.calls), 1)

    def test_ids_selection(self):
        self.call("--ids", str(self.de.pk))
        self.assertEqual(self.intel.calls, ["203.0.113.9"])

    def test_hosts_need_not_be_in_the_inventory(self):
        intel = FakeIpIntel({"203.0.113.77": facts("203.0.113.77", asn=64999)})
        self.call("--hosts", "203.0.113.77", intel=intel)
        record = IpIntelRecord.objects.get(query="203.0.113.77")
        self.assertEqual(record.asn, 64999)
        self.assertFalse(Proxy.objects.filter(host="203.0.113.77").exists())

    def test_country_filter(self):
        self.call("--country", "nl")
        self.assertEqual(self.intel.calls, ["198.51.100.4"])

    def test_an_unavailable_source_is_reported_as_ours_not_the_inventorys(self):
        output = self.call(intel=FakeIpIntel(unavailable=True), verbosity=1)
        self.assertIn("source unavailable", output)
        self.assertEqual(IpIntelRecord.objects.count(), 0)

    def test_stalest_first(self):
        """A `--limit` run must spend its budget on the records that have
        been wrong for longest, and a host with no record at all is the
        stalest thing there is."""
        store_facts(facts("198.51.100.4"), now=timezone.now())
        self.call("--force", "--limit", "1")
        self.assertEqual(self.intel.calls, ["203.0.113.9"])
