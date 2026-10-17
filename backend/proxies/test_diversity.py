"""Chain diversity scoring.

**Nothing here touches the network.** Every fact about an address is a row
written directly into `IpIntelRecord`, or a `FakeIpIntel` answer -- the same
rule `test_health.py` follows for probing, for the same reasons: a test that
needs a live lookup fails in CI, fails offline, and fails in the sandbox
this project is developed in.

The assertions are mostly about *ordering* and *refusal*. A chain-quality
feature that over-reports is noise an operator turns off; one that
under-reports is worse than nothing, because it hands out reassurance that
was never computed. So there are as many tests here for "did not claim
anything" as for "found the problem".
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from proxies.diversity import (
    SEVERITY_ORDER,
    DiversityReport,
    HopInput,
    Severity,
    analyse_chain,
    analyse_hops,
    analyse_proxies,
    pseudonymise_providers,
    score_findings,
)
from proxies.jurisdictions import (
    blocs,
    blocs_containing,
    normalise_country,
    smallest_bloc_containing,
)
from proxies.models import (
    ChainHop,
    entity_key,
    IpIntelRecord,
    IpIntelStatus,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
)
from django.contrib.auth import get_user_model

User = get_user_model()


def write_intel(
    host,
    *,
    asn=None,
    org="",
    prefix="",
    country="",
    ip=None,
    registry="arin",
    status=IpIntelStatus.OK,
    fresh=True,
    source="fake",
):
    """One cached lookup. `fresh=False` writes an already-expired row."""
    now = timezone.now()
    return IpIntelRecord.objects.create(
        query=host.lower(),
        ip=ip or (host if _is_ip(host) else None),
        asn=asn,
        asn_org=org,
        prefix=prefix,
        country=country,
        registry=registry,
        status=status,
        source=source,
        looked_up_at=now - timedelta(days=1),
        expires_at=now + (timedelta(days=29) if fresh else -timedelta(hours=1)),
    )


def _is_ip(value):
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() for part in parts)


def hops(*specs):
    """`HopInput`s from `(host, provider, country)` triples."""
    return [
        HopInput(
            position=index,
            host=spec[0],
            provider=spec[1] if len(spec) > 1 else "",
            country=spec[2] if len(spec) > 2 else "",
        )
        for index, spec in enumerate(specs)
    ]


def codes(report):
    return [finding.code for finding in report.findings]


def finding(report, code):
    for item in report.findings:
        if item.code == code:
            return item
    raise AssertionError(f"no {code} finding in {codes(report)}")


class EntryExitCorrelationTests(TestCase):
    """Check 1 -- the headline, and the one that must outrank everything."""

    def test_entry_and_exit_on_one_asn_is_critical(self):
        write_intel("10.0.0.1", asn=64500, org="Acme", prefix="10.0.0.0/16", country="NL")
        write_intel("10.9.0.1", asn=64500, org="Acme", prefix="10.9.0.0/16", country="DE")
        write_intel("172.16.0.1", asn=64501, org="Other", prefix="172.16.0.0/16", country="FR")

        report = analyse_hops(hops(("10.0.0.1",), ("172.16.0.1",), ("10.9.0.1",)))

        item = finding(report, "entry_exit_shared_asn")
        self.assertEqual(item.severity, Severity.CRITICAL)
        self.assertEqual(item.positions, (0, 2))
        self.assertEqual(item.detail["asn"], 64500)
        self.assertIn("both", item.message)

    def test_entry_exit_outranks_middle_hop_sharing(self):
        """The ordering requirement, asserted three ways.

        A chain where hops 0 and 2 share an ASN must be reported worse
        than one where hops 0 and 1 do -- by severity, by sort position
        and by score. One party seeing both ends defeats the chain; two
        middle hops sharing a network merely weakens it.
        """
        # Chain A: entry and exit share. Chain B: entry and middle share.
        write_intel("10.0.0.1", asn=64500, org="Acme", prefix="10.0.0.0/16")
        write_intel("10.9.0.1", asn=64500, org="Acme", prefix="10.9.0.0/16")
        write_intel("172.16.0.1", asn=64501, org="Other", prefix="172.16.0.0/16")
        write_intel("192.0.2.1", asn=64502, org="Third", prefix="192.0.2.0/24")

        # Distinct vendors on every hop, so the only thing either chain
        # is flagged for is the ASN relationship and the comparison is
        # between like and like.
        entry_exit = analyse_hops(
            hops(("10.0.0.1", "v1"), ("172.16.0.1", "v2"), ("10.9.0.1", "v3"))
        )
        middle = analyse_hops(
            hops(("10.0.0.1", "v1"), ("10.9.0.1", "v2"), ("192.0.2.1", "v3"))
        )

        self.assertEqual(entry_exit.worst_severity, Severity.CRITICAL)
        self.assertEqual(middle.worst_severity, Severity.MEDIUM)
        self.assertLess(
            SEVERITY_ORDER[entry_exit.worst_severity],
            SEVERITY_ORDER[middle.worst_severity],
        )
        self.assertLess(entry_exit.score, middle.score)
        # And the critical finding sorts to the top of its own list.
        self.assertEqual(entry_exit.findings[0].code, "entry_exit_shared_asn")

    def test_entry_exit_same_network_reported_instead_of_same_asn(self):
        """One fact, one finding. Two hops in a /24 are obviously also on
        one ASN, and saying both would be two criticals for one problem."""
        write_intel("10.0.0.1", asn=64500, org="Acme", prefix="10.0.0.0/24")
        write_intel("10.0.0.9", asn=64500, org="Acme", prefix="10.0.0.0/24")
        write_intel("172.16.0.1", asn=64501, org="Other", prefix="172.16.0.0/16")

        report = analyse_hops(hops(("10.0.0.1",), ("172.16.0.1",), ("10.0.0.9",)))

        self.assertIn("entry_exit_shared_network", codes(report))
        self.assertNotIn("entry_exit_shared_asn", codes(report))

    def test_entry_exit_same_org_different_asn(self):
        write_intel("10.0.0.1", asn=64500, org="Acme Hosting", prefix="10.0.0.0/16")
        write_intel("10.9.0.1", asn=64600, org="acme   hosting", prefix="10.9.0.0/16")
        write_intel("172.16.0.1", asn=64501, org="Other", prefix="172.16.0.0/16")

        report = analyse_hops(hops(("10.0.0.1",), ("172.16.0.1",), ("10.9.0.1",)))

        item = finding(report, "entry_exit_shared_asn_org")
        self.assertEqual(item.severity, Severity.CRITICAL)
        self.assertEqual(sorted(item.detail["asns"]), [64500, 64600])

    def test_registry_punctuation_does_not_hide_one_operator(self):
        """Found by running the analysis against the dev database.

        Regional registries write `GLOBAL-TRANSIT-LLC` where a human
        writes `Global Transit LLC`, so two AS numbers belonging to one
        company routinely differ only by punctuation. Comparing the raw
        strings reported them as independent operators -- a clean 100/100
        for a chain whose entry and exit were the same company, which is
        the exact failure this feature exists to prevent.
        """
        write_intel("10.0.0.1", asn=64700, org="GLOBAL-TRANSIT-LLC", prefix="10.0.0.0/24", country="US")
        write_intel("10.9.0.1", asn=64701, org="Global Transit LLC", prefix="10.9.0.0/24", country="GB")
        write_intel("172.16.0.1", asn=64800, org="Southern IX", prefix="172.16.0.0/24", country="BR")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "Seller One", "US"),
                ("172.16.0.1", "Seller Two", "BR"),
                ("10.9.0.1", "Seller Three", "GB"),
            )
        )

        item = finding(report, "entry_exit_shared_asn_org")
        self.assertEqual(item.severity, Severity.CRITICAL)
        self.assertEqual(sorted(item.detail["asns"]), [64700, 64701])
        self.assertLess(report.score, 50)

    def test_entity_key_normalisation(self):
        self.assertEqual(entity_key("GLOBAL-TRANSIT-LLC"), "global transit llc")
        self.assertEqual(entity_key(" Web  Share "), "web share")
        self.assertEqual(entity_key("Acme, Inc."), "acme inc")
        self.assertEqual(entity_key(""), "")
        self.assertEqual(entity_key(None), "")
        # Not everything collapses: these stay two different companies.
        self.assertNotEqual(entity_key("Acme LLC"), entity_key("Acme Inc"))

    def test_a_clean_three_hop_chain_has_no_critical_finding(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="BR")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="JP")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "Vendor one", "NL"),
                ("172.16.0.1", "Vendor two", "BR"),
                ("192.0.2.1", "Vendor three", "JP"),
            )
        )

        self.assertEqual(report.findings, [])
        self.assertEqual(report.score, 100.0)
        self.assertIsNone(report.worst_severity)


class ProviderCorrelationTests(TestCase):
    """Check 3 -- the one an ASN lookup structurally cannot do."""

    def test_same_provider_different_asn_is_detected(self):
        write_intel("10.0.0.1", asn=64500, org="Acme", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="Beta", prefix="172.16.0.0/24", country="BR")
        write_intel("192.0.2.1", asn=64502, org="Gamma", prefix="192.0.2.0/24", country="JP")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "Reseller X", "NL"),
                ("172.16.0.1", "Reseller X", "BR"),
                ("192.0.2.1", "Someone else", "JP"),
            )
        )

        item = finding(report, "shared_provider")
        self.assertEqual(item.severity, Severity.MEDIUM)
        self.assertEqual(item.positions, (0, 1))
        self.assertEqual(item.detail["distinct_asns"], 2)
        # Three different ASNs, three different countries: nothing else
        # fires. The ONLY reason this chain is flagged is the vendor.
        self.assertEqual(
            [code for code in codes(report) if code.startswith("shared_")],
            ["shared_provider"],
        )

    def test_entry_and_exit_from_one_vendor_is_critical_even_on_separate_asns(self):
        write_intel("10.0.0.1", asn=64500, org="Acme", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="Beta", prefix="172.16.0.0/24", country="BR")
        write_intel("192.0.2.1", asn=64502, org="Gamma", prefix="192.0.2.0/24", country="JP")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "Reseller X", "NL"),
                ("172.16.0.1", "Other", "BR"),
                ("192.0.2.1", "reseller x ", "JP"),
            )
        )

        item = finding(report, "entry_exit_shared_provider")
        self.assertEqual(item.severity, Severity.CRITICAL)
        self.assertFalse(item.detail["same_asn"])
        self.assertLess(report.score, 60)
        # One fact, one finding -- and therefore one penalty. The medium
        # `shared_provider` for the same pair is suppressed, or the score
        # would be charged twice for a single relationship.
        self.assertNotIn("shared_provider", codes(report))
        self.assertEqual(report.score, 50.0)

    def test_a_vendor_group_wider_than_the_entry_exit_pair_is_still_reported(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="BR")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="JP")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "One", "NL"),
                ("172.16.0.1", "One", "BR"),
                ("192.0.2.1", "One", "JP"),
            )
        )

        self.assertIn("entry_exit_shared_provider", codes(report))
        # All three, not just the pair: that is more than the critical
        # finding said, so it is not a duplicate.
        self.assertEqual(finding(report, "shared_provider").positions, (0, 1, 2))

    def test_two_blank_providers_are_not_two_vendors(self):
        """The provider half of "unknown is not a pass"."""
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="BR")

        report = analyse_hops(hops(("10.0.0.1", "", "NL"), ("172.16.0.1", "", "BR")))

        self.assertNotIn("shared_provider", codes(report))
        item = finding(report, "provider_unrecorded")
        self.assertEqual(item.severity, Severity.UNKNOWN)
        self.assertEqual(item.positions, (0, 1))


class NetworkBlockTests(TestCase):
    """Check 4 -- same /24, or same announced prefix."""

    def test_same_slash_24_is_detected_even_when_the_announced_prefix_is_wide(self):
        """The announced prefix can be a /16. Two addresses in one /24
        inside it is the sharper claim, and the sharper claim is the one
        reported."""
        write_intel("198.51.100.4", asn=64500, org="A", prefix="198.51.0.0/16")
        write_intel("198.51.100.9", asn=64500, org="A", prefix="198.51.0.0/16")
        write_intel("203.0.113.1", asn=64501, org="B", prefix="203.0.113.0/24")

        report = analyse_hops(
            hops(("198.51.100.4",), ("198.51.100.9",), ("203.0.113.1",))
        )

        item = finding(report, "shared_network")
        self.assertEqual(item.severity, Severity.HIGH)
        self.assertEqual(item.positions, (0, 1))
        self.assertEqual(item.detail["network"], "198.51.100.0/24")

    def test_network_finding_outranks_the_asn_finding_for_the_same_pair(self):
        write_intel("198.51.100.4", asn=64500, org="A", prefix="198.51.100.0/24")
        write_intel("198.51.100.9", asn=64500, org="A", prefix="198.51.100.0/24")
        write_intel("203.0.113.1", asn=64501, org="B", prefix="203.0.113.0/24")

        report = analyse_hops(
            hops(("198.51.100.4",), ("198.51.100.9",), ("203.0.113.1",))
        )

        self.assertIn("shared_network", codes(report))
        self.assertNotIn("shared_asn", codes(report))

    def test_asn_group_wider_than_the_network_group_is_still_reported(self):
        """Two hops in one /24 and a third elsewhere on the same ASN is
        two facts, and the wider one is not implied by the narrower."""
        write_intel("198.51.100.4", asn=64500, org="A", prefix="198.51.100.0/24")
        write_intel("198.51.100.9", asn=64500, org="A", prefix="198.51.100.0/24")
        write_intel("198.51.7.1", asn=64500, org="A", prefix="198.51.7.0/24")

        report = analyse_hops(
            hops(("198.51.100.4",), ("198.51.100.9",), ("198.51.7.1",))
        )

        self.assertEqual(finding(report, "shared_network").positions, (0, 1))
        self.assertEqual(finding(report, "shared_asn").positions, (0, 1, 2))


class JurisdictionTests(TestCase):
    """Check 5, and the bloc map that drives it."""

    def test_all_hops_in_one_country(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="NL")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="NL")

        report = analyse_hops(
            hops(("10.0.0.1", "a"), ("172.16.0.1", "b"), ("192.0.2.1", "c"))
        )

        item = finding(report, "jurisdiction_single_country")
        self.assertEqual(item.severity, Severity.MEDIUM)
        self.assertEqual(item.detail["country"], "NL")

    def test_different_countries_inside_one_bloc(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="US")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="GB")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="CA")

        report = analyse_hops(
            hops(("10.0.0.1", "a"), ("172.16.0.1", "b"), ("192.0.2.1", "c"))
        )

        item = finding(report, "jurisdiction_single_bloc")
        self.assertEqual(item.severity, Severity.LOW)
        # Most specific bloc, not merely *a* bloc that contains them.
        self.assertEqual(item.detail["bloc"], "five_eyes")
        self.assertNotIn("jurisdiction_single_country", codes(report))

    def test_genuinely_spread_chain_raises_no_jurisdiction_finding(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="BR")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="JP")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="ZA")

        report = analyse_hops(
            hops(("10.0.0.1", "a"), ("172.16.0.1", "b"), ("192.0.2.1", "c"))
        )

        self.assertNotIn("jurisdiction_single_bloc", codes(report))
        self.assertNotIn("jurisdiction_single_country", codes(report))

    def test_no_jurisdiction_claim_when_a_country_is_unknown(self):
        """Two hops in NL and one unknown must not read as 'not
        concentrated'. The analysis stays silent and the unknown finding
        carries the truth."""
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="NL")
        write_intel(
            "192.0.2.1", status=IpIntelStatus.NOT_FOUND, prefix="", country=""
        )

        report = analyse_hops(
            hops(("10.0.0.1", "a"), ("172.16.0.1", "b"), ("192.0.2.1", "c"))
        )

        self.assertNotIn("jurisdiction_single_country", codes(report))
        self.assertIn("hop_intel_unknown", codes(report))

    def test_operator_country_is_used_but_marked_as_the_vendors_claim(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="NL")

        report = analyse_hops(
            hops(("10.0.0.1", "a", "Netherlands"), ("172.16.0.1", "b", "NL"))
        )

        self.assertEqual(report.hops[0].country, "NL")
        self.assertEqual(report.hops[0].country_source, "operator")
        self.assertEqual(report.hops[1].country_source, "intel")
        item = finding(report, "jurisdiction_single_country")
        self.assertEqual(item.detail["operator_reported_positions"], [0])

    @override_settings(
        PROXY_JURISDICTION_BLOCS={
            "my_bloc": {"label": "My Bloc", "countries": ["BR", "JP"]}
        }
    )
    def test_bloc_map_is_configuration_not_code(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="BR")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="JP")

        report = analyse_hops(hops(("10.0.0.1", "a"), ("172.16.0.1", "b")))

        self.assertEqual(finding(report, "jurisdiction_single_bloc").detail["bloc"], "my_bloc")
        # The override REPLACES the defaults rather than merging, so an
        # operator who disagrees with a shipped grouping can remove it.
        self.assertEqual(set(blocs()), {"my_bloc"})

    def test_country_normalisation(self):
        self.assertEqual(normalise_country("nl"), "NL")
        self.assertEqual(normalise_country(" Netherlands "), "NL")
        self.assertEqual(normalise_country("Atlantis"), "")
        self.assertEqual(normalise_country(""), "")

    def test_smallest_bloc_wins(self):
        self.assertEqual(smallest_bloc_containing(["US", "GB"])["key"], "five_eyes")
        self.assertEqual(smallest_bloc_containing(["US", "DE"])["key"], "fourteen_eyes")
        self.assertIsNone(smallest_bloc_containing(["BR", "JP"]))
        # A partly-unknown set is not a bloc claim.
        self.assertEqual(blocs_containing(["US", ""]), [])


class UnknownDataTests(TestCase):
    """Check 7. The most important tests in the file.

    'Unknown' must never be scored, sorted or summarised as if it were a
    pass. Each of these asserts a different route by which it could.
    """

    def test_a_chain_with_no_intel_at_all_is_not_a_clean_chain(self):
        report = analyse_hops(
            hops(("10.0.0.1", "a"), ("172.16.0.1", "b"), ("192.0.2.1", "c"))
        )

        self.assertEqual(report.worst_severity, Severity.UNKNOWN)
        self.assertNotEqual(report.score, 100.0)
        self.assertEqual(report.score, 50.0)  # the coverage ceiling
        item = finding(report, "hop_intel_unknown")
        self.assertEqual(item.positions, (0, 1, 2))
        self.assertEqual(item.detail["known"], 0)
        self.assertIn("not a pass", item.message)

    def test_partial_coverage_caps_the_score_proportionally(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="BR")

        report = analyse_hops(
            hops(("10.0.0.1", "a", "NL"), ("172.16.0.1", "b", "BR"), ("192.0.2.1", "c", "JP"))
        )

        # Two of three hops known: nothing found between the two that
        # were checked, but the chain cannot score as if all three were.
        self.assertEqual(report.coverage, 2 / 3)
        self.assertAlmostEqual(report.score, 50.0 + 50.0 * (2 / 3), places=4)
        self.assertLess(report.score, 100.0)

    def test_unknown_outranks_high_and_medium_in_the_severity_order(self):
        self.assertLess(SEVERITY_ORDER[Severity.UNKNOWN], SEVERITY_ORDER[Severity.HIGH])
        self.assertLess(SEVERITY_ORDER[Severity.UNKNOWN], SEVERITY_ORDER[Severity.MEDIUM])
        self.assertLess(
            SEVERITY_ORDER[Severity.CRITICAL], SEVERITY_ORDER[Severity.UNKNOWN]
        )

    def test_summary_for_an_unchecked_chain_says_unverified_not_fine(self):
        report = analyse_hops(hops(("10.0.0.1", "a"), ("172.16.0.1", "b")))
        self.assertIn("unverified", report.summary())
        self.assertNotIn("No shared", report.summary())

    def test_an_expired_record_is_unknown_not_stale_data(self):
        write_intel(
            "10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", fresh=False
        )
        write_intel("172.16.0.1", asn=64500, org="A", prefix="10.0.0.0/24")

        report = analyse_hops(hops(("10.0.0.1", "a"), ("172.16.0.1", "b")))

        self.assertFalse(report.hops[0].intel_known)
        self.assertEqual(report.hops[0].intel_status, "missing")
        # Both hops are in fact on AS64500, but the expired row is not
        # used to say so. Silence beats a claim from data we agreed to
        # stop trusting.
        self.assertNotIn("entry_exit_shared_asn", codes(report))
        self.assertIn("hop_intel_unknown", codes(report))

    def test_a_not_found_record_is_unknown_rather_than_distinct(self):
        write_intel("10.0.0.1", status=IpIntelStatus.NOT_FOUND)
        write_intel("172.16.0.1", status=IpIntelStatus.NOT_FOUND)

        report = analyse_hops(hops(("10.0.0.1", "a"), ("172.16.0.1", "b")))

        self.assertEqual(report.counts()["asns"], 0)
        self.assertEqual(report.worst_severity, Severity.UNKNOWN)
        self.assertEqual(finding(report, "hop_intel_unknown").detail["statuses"], ["not_found"])

    def test_distinct_counts_exclude_unknowns(self):
        """A count of 'how many different ASNs' must not be inflated by
        the hops nobody looked up."""
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")

        report = analyse_hops(hops(("10.0.0.1", "a", "NL"), ("172.16.0.1", "", "")))

        self.assertEqual(report.counts()["asns"], 1)
        self.assertEqual(report.counts()["countries"], 1)
        self.assertEqual(report.counts()["providers"], 1)


class ShapeTests(TestCase):
    """Check 6, plus the degenerate cases."""

    def test_single_hop_is_reported_as_a_proxy_not_a_chain(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")

        report = analyse_hops(hops(("10.0.0.1", "Vendor", "NL")))

        item = finding(report, "single_hop")
        self.assertEqual(item.severity, Severity.INFO)
        self.assertIn("not a chain", item.title)
        # Scored at zero regardless of how clean the data is: with one hop
        # there is one party and it is both ends.
        self.assertEqual(report.score, 0.0)
        self.assertIn("both your address and your destination", report.summary())

    def test_empty_chain(self):
        report = analyse_hops([])
        self.assertEqual(codes(report), ["empty_chain"])
        self.assertEqual(report.score, 0.0)
        self.assertEqual(report.coverage, 0.0)

    def test_two_hop_chain_reports_the_pair_once(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/16")
        write_intel("10.9.0.1", asn=64500, org="A", prefix="10.9.0.0/16")

        report = analyse_hops(hops(("10.0.0.1", "v"), ("10.9.0.1", "v")))

        self.assertEqual(
            sorted(codes(report)),
            ["entry_exit_shared_asn", "entry_exit_shared_provider"],
        )

    def test_messages_number_hops_from_one_while_positions_stay_zero_based(self):
        """Prose counts from 1; the payload counts from 0.

        Found in the running app: a card's header read "Hops 1, 2 and 3"
        (the client labels the first hop "Hop 1") while the server's own
        message underneath read "Hops 0, 1 and 2" -- one card contradicting
        itself. `positions` must stay 0-based because clients index with
        it, so only the prose changed.
        """
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="BR")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="JP")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "One Vendor", "NL"),
                ("172.16.0.1", "One Vendor", "BR"),
                ("192.0.2.1", "One Vendor", "JP"),
            )
        )

        item = finding(report, "shared_provider")
        self.assertEqual(list(item.positions), [0, 1, 2], "payload stays 0-based")
        self.assertIn("Hops 1, 2 and 3", item.message)

        # No emitted message may number a hop from zero.
        for other in report.findings:
            self.assertNotIn(
                "Hops 0", other.message or "", f"{other.code} numbers from zero"
            )
            self.assertNotIn("Hop 0 ", other.message or "", other.code)


class ScoringTests(TestCase):
    def test_score_is_derived_from_findings_and_cannot_disagree_with_them(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/16", country="NL")
        write_intel("10.9.0.1", asn=64500, org="A", prefix="10.9.0.0/16", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/16", country="NL")

        report = analyse_hops(
            hops(
                ("10.0.0.1", "v1", "NL"),
                ("172.16.0.1", "v2", "NL"),
                ("10.9.0.1", "v3", "NL"),
            )
        )

        expected = 100.0 - sum(item.penalty for item in report.findings)
        self.assertEqual(report.score, max(0.0, expected))
        self.assertEqual(report.worst_severity, Severity.CRITICAL)

    def test_score_never_goes_below_zero(self):
        report = DiversityReport(hops=[], findings=[], score=0.0)
        self.assertEqual(report.score, 0.0)

        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("10.0.0.2", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("10.0.0.3", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")

        worst = analyse_hops(
            hops(
                ("10.0.0.1", "One", "NL"),
                ("10.0.0.2", "One", "NL"),
                ("10.0.0.3", "One", "NL"),
            )
        )
        self.assertEqual(worst.score, 0.0)

    def test_an_unknown_chain_cannot_outscore_a_measured_clean_one(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="BR")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="JP")

        measured = analyse_hops(
            hops(("10.0.0.1", "a", "BR"), ("172.16.0.1", "b", "JP"))
        )
        unmeasured = analyse_hops(hops(("203.0.113.5", "a"), ("203.0.113.6", "b")))

        self.assertGreater(measured.score, unmeasured.score)

    def test_score_findings_is_a_pure_function(self):
        self.assertEqual(score_findings([], []), 0.0)


class VocabularyTests(TestCase):
    """The product must not imply a guarantee it cannot make."""

    def test_the_payload_carries_the_correlation_resistance_note(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="BR")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="JP")

        payload = analyse_hops(
            hops(("10.0.0.1", "a", "BR"), ("172.16.0.1", "b", "JP"))
        ).as_dict()

        note = payload["vocabulary_note"]
        self.assertIn("correlation resistance", note)
        self.assertIn("not anonymity", note)

    def test_no_finding_promises_anonymity_or_safety(self):
        """A blunt grep over every string the module can emit. Cheap, and
        it fails the moment somebody writes 'you are anonymous'."""
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/16", country="US")
        write_intel("10.9.0.1", asn=64500, org="A", prefix="10.9.0.0/16", country="GB")
        write_intel("172.16.0.1", asn=64600, org="A", prefix="172.16.0.0/16", country="CA")

        reports = [
            analyse_hops(hops(("10.0.0.1", "v", "US"))),
            analyse_hops(
                hops(
                    ("10.0.0.1", "v", "US"),
                    ("172.16.0.1", "w", "CA"),
                    ("10.9.0.1", "v", "GB"),
                )
            ),
            analyse_hops(hops(("203.0.113.1", "", ""), ("203.0.113.2", "", ""))),
        ]
        banned = ("anonymous", "anonymity", "untraceable", "guarantee", "you are safe")
        for report in reports:
            text = " ".join(
                [report.summary()]
                + [item.title for item in report.findings]
                + [item.message for item in report.findings]
            ).lower()
            for word in banned:
                self.assertNotIn(word, text, f"{word!r} in {text!r}")


class ProviderPseudonymTests(TestCase):
    """P4: the mobile client learns the *relationship*, never the vendor."""

    def test_real_vendor_names_never_reach_a_pseudonymised_report(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="BR")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="JP")
        write_intel("192.0.2.1", asn=64502, org="C", prefix="192.0.2.0/24", country="ZA")

        entries = hops(
            ("10.0.0.1", "Webshare", "BR"),
            ("172.16.0.1", "Webshare", "JP"),
            ("192.0.2.1", "Bright Data", "ZA"),
        )
        report = analyse_hops(pseudonymise_providers(entries))
        blob = str(report.as_dict())

        self.assertNotIn("Webshare", blob)
        self.assertNotIn("Bright Data", blob)
        self.assertIn("Vendor A", blob)
        # The relationship survives the substitution -- which is the point.
        self.assertEqual(finding(report, "shared_provider").positions, (0, 1))

    def test_pseudonyms_are_stable_within_one_analysis(self):
        entries = pseudonymise_providers(
            hops(("a", "X"), ("b", "Y"), ("c", "x "), ("d", ""))
        )
        self.assertEqual(
            [entry.provider for entry in entries],
            ["Vendor A", "Vendor B", "Vendor A", ""],
        )


class ChainIntegrationTests(TestCase):
    """The same analysis over real `Proxy` and `ProxyChain` rows."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="chain@example.com", password="Str0ngPassphrase!"
        )

    def _proxy(self, host, **kwargs):
        defaults = {
            "protocol": ProxyProtocol.SOCKS5,
            "port": 1080,
            "tier": ProxyTier.PREMIUM,
        }
        defaults.update(kwargs)
        return Proxy.objects.create(host=host, **defaults)

    def test_analyse_proxies_reads_the_provider_column(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        write_intel("172.16.0.1", asn=64501, org="B", prefix="172.16.0.0/24", country="BR")
        first = self._proxy("10.0.0.1", provider="Acme", country="NL")
        second = self._proxy("172.16.0.1", provider="acme", country="BR")

        report = analyse_proxies([first, second])

        self.assertEqual(finding(report, "entry_exit_shared_provider").severity, Severity.CRITICAL)
        self.assertEqual(report.hops[0].proxy_id, first.pk)

    def test_constrained_hops_are_reported_as_unresolved_not_diverse(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/24", country="NL")
        proxy = self._proxy("10.0.0.1", provider="Acme", country="NL")
        chain = ProxyChain.objects.create(user=self.user, name="mixed")
        ChainHop.objects.create(chain=chain, proxy=proxy, position=0)
        ChainHop.objects.create(chain=chain, position=1, country="BR")

        report = analyse_chain(chain)

        item = finding(report, "hop_unresolved")
        self.assertEqual(item.severity, Severity.UNKNOWN)
        self.assertEqual(item.positions, (1,))
        self.assertLess(report.score, 100.0)

    def test_a_resolved_chain_is_analysed_as_what_will_be_connected(self):
        write_intel("10.0.0.1", asn=64500, org="A", prefix="10.0.0.0/16", country="NL")
        write_intel("10.9.0.1", asn=64500, org="A", prefix="10.9.0.0/16", country="NL")
        first = self._proxy("10.0.0.1", provider="Acme", country="NL")
        second = self._proxy("10.9.0.1", provider="Acme", country="NL")
        chain = ProxyChain.objects.create(user=self.user, name="resolved")
        ChainHop.objects.create(chain=chain, position=0, country="NL")
        ChainHop.objects.create(chain=chain, position=1, country="NL")

        report = analyse_chain(chain, resolved_proxies=[first, second])

        self.assertIn("entry_exit_shared_asn", codes(report))
        self.assertNotIn("hop_unresolved", codes(report))


class ProviderCsvImportTests(TestCase):
    """The `provider` column, imported.

    These live here rather than with the other CSV tests because the
    failure they guard is a diversity failure, not an import one: a
    silently blanked provider turns "hops 1 and 3 are the same vendor"
    into "different vendors", which is precisely the false reassurance
    this feature exists to withhold. The absent-vs-empty rule from
    `proxies.importers` is what prevents it.
    """

    def import_csv(self, text, name="p.csv"):
        import io

        from proxies.importers import import_proxies_from_csv

        return import_proxies_from_csv(io.BytesIO(text.encode()), name)

    def test_provider_is_imported(self):
        self.import_csv(
            "protocol,host,port,provider\n"
            "socks5,198.51.100.60,1080,Webshare\n"
        )
        self.assertEqual(Proxy.objects.get().provider, "Webshare")

    def test_vendor_and_seller_are_accepted_spellings(self):
        self.import_csv(
            "protocol,host,port,vendor\nsocks5,198.51.100.61,1080,Acme\n", "v.csv"
        )
        self.import_csv(
            "protocol,host,port,seller\nhttp,198.51.100.62,8080,Beta\n", "s.csv"
        )
        self.assertEqual(Proxy.objects.get(host="198.51.100.61").provider, "Acme")
        self.assertEqual(Proxy.objects.get(host="198.51.100.62").provider, "Beta")

    def test_a_file_without_the_column_leaves_an_existing_label_alone(self):
        self.import_csv(
            "protocol,host,port,provider\nsocks5,198.51.100.63,1080,Acme\n", "a.csv"
        )
        self.import_csv(
            "protocol,host,port,country\nsocks5,198.51.100.63,1080,NL\n", "b.csv"
        )
        proxy = Proxy.objects.get(host="198.51.100.63")
        self.assertEqual(proxy.provider, "Acme")
        self.assertEqual(proxy.country, "NL")

    def test_an_empty_cell_in_a_present_column_is_a_deliberate_clear(self):
        self.import_csv(
            "protocol,host,port,provider\nsocks5,198.51.100.64,1080,Acme\n", "a.csv"
        )
        self.import_csv(
            "protocol,host,port,provider\nsocks5,198.51.100.64,1080,\n", "b.csv"
        )
        self.assertEqual(Proxy.objects.get(host="198.51.100.64").provider, "")

    def test_provider_key_normalises_for_comparison_but_not_for_storage(self):
        proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.65",
            port=1080,
            provider="  Web   Share ",
        )
        self.assertEqual(proxy.provider, "  Web   Share ")
        self.assertEqual(proxy.provider_key, "web share")
