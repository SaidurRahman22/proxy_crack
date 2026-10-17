"""Chain diversity: whether a chain is three operators or one wearing three
brands.

proxychains will build a three-hop chain whose hops are three addresses in
one rack, on one ASN, under one company, billed to one card -- and say
nothing. The user believes they bought three independent observers. They
bought one. This module is the thing that says so before they connect.

The word for what is measured
-----------------------------
**Correlation resistance**, not anonymity. Chaining commercial proxies
bought under one account with one payment trail is not Tor and this project
has said so in writing (`memory.md`, "Positions taken"). A high score here
means *these particular hops do not obviously share a network, an operator
or a jurisdiction*. It does not mean traffic cannot be correlated: it says
nothing about timing analysis, about netflow at a transit provider, about
the payment trail that bought all three, or about what any one of these
companies does with its logs. Every string this module emits is written to
survive being quoted back at us.

Findings, not a number
----------------------
A bare score tells an operator nothing they can act on. 62/100 does not say
whether to swap hop 3 or to stop using a vendor. So the output is a list of
**named findings with severities**, and the score is derived from them --
the reverse of the usual arrangement, and the reason the score can never
disagree with the findings.

Severity, and why "unknown" is one of them
------------------------------------------
    critical  the chain's purpose is defeated       (entry/exit correlation)
    unknown   the check could not be run at all
    high      hops are very likely the same machine room
    medium    hops share a network or a vendor
    low       a jurisdictional concentration worth knowing about
    info      a fact about the shape of the chain, not a defect

`unknown` sits second, above `high`, and that placement is the single most
important decision in this file. A hop with no ASN data is **not** a
diverse hop. It is a hop nobody has checked, and an operator can act on a
known problem far more easily than on an unmeasured one -- the known
problem names the hop to replace, while the unmeasured one does not even
say whether there is a problem. Everything here is built so that missing
data degrades the answer loudly: the hop is listed as unknown, a finding
says so, and `coverage` caps the score (see `_coverage_ceiling`).

The checks, in the order they matter
------------------------------------
1. **Entry/exit correlation.** If the first hop and the last hop share a
   network, an AS, an AS operator or a vendor, that single entity sees the
   user's real address *and* their destination. There is no remaining
   benefit from the chain -- the middle hops only add latency. This is
   categorically worse than two middle hops sharing a network, which is
   why it has a severity of its own rather than a larger penalty.
2. **Any pair on one ASN.** Correlation, but at least not end-to-end.
3. **Any pair from one vendor.** An ASN says which network an address sits
   on. It does not say who was paid. A reseller fronting someone else's
   network is invisible to an ASN check, and the proxy market is
   consolidated enough that this is the normal case rather than the
   exotic one. One vendor is one account to close and one entity to
   subpoena, whatever the routing says.
4. **Any pair in one network block.** Same announced prefix, or same /24.
   Near-certainly the same physical infrastructure.
5. **Jurisdiction concentration.** All hops in one country, or all inside
   one legal bloc. The bloc map is configuration, not code -- see
   `proxies.jurisdictions`.
6. **Single-hop chains.** Reported honestly: that is a proxy, not a chain.
7. **Unknown data.** Never silently scored as diverse. See above.

What is deliberately not here
-----------------------------
No check for shared upstream transit. Two ASNs that both buy transit from
the same tier-1 carrier are correlated in a way nothing below detects,
and finding out needs BGP path data this project does not have. Saying so
here is better than implying the list is exhaustive.
"""

import ipaddress
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from proxies.intel import cached_facts, normalise_host, parse_ip
from proxies.jurisdictions import normalise_country, smallest_bloc_containing
from proxies.models import entity_key, provider_key

__all__ = [
    "DiversityReport",
    "Finding",
    "HopInput",
    "HopIntel",
    "SEVERITY_ORDER",
    "Severity",
    "analyse_chain",
    "analyse_hops",
    "analyse_proxies",
    "hop_inputs_from_proxies",
    "pseudonymise_providers",
]


# The sentence that goes out with every analysis. Kept as a constant and
# served by the API so the client cannot render a score without it.
VOCABULARY_NOTE = (
    "This measures correlation resistance between the hops you chose -- "
    "whether they share a network, an operator or a jurisdiction. It is "
    "not anonymity. Commercial proxies bought under one account with one "
    "payment trail can still be correlated by timing, by transit "
    "providers, or by the vendors' own records."
)


class Severity:
    """Not an enum: these are strings in a JSON payload and a client
    compares them. Keeping them plain makes that contract obvious."""

    CRITICAL = "critical"
    UNKNOWN = "unknown"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


#: Display and sort order. `unknown` above `high` is deliberate -- see the
#: module docstring. Lower number sorts first.
SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.UNKNOWN: 1,
    Severity.HIGH: 2,
    Severity.MEDIUM: 3,
    Severity.LOW: 4,
    Severity.INFO: 5,
}


# Score cost per finding. Scores are derived from findings rather than the
# other way round, so changing a number here cannot make the score and the
# finding list tell different stories.
#
# The entry/exit costs are large enough that any one of them alone puts a
# chain below half marks, because any one of them alone means the chain is
# not doing the job it was built for.
PENALTIES = {
    "entry_exit_shared_network": 70,
    "entry_exit_shared_asn": 60,
    "entry_exit_shared_asn_org": 55,
    "entry_exit_shared_provider": 50,
    "shared_network": 30,
    "shared_asn": 20,
    "shared_asn_org": 18,
    "shared_provider": 18,
    "jurisdiction_single_country": 12,
    "jurisdiction_single_bloc": 8,
}

#: Score ceiling for a chain nothing is known about. Mirrors
#: `PROXY_UNPROBED_SCORE` and D15 on purpose: "we have not measured this"
#: belongs below anything proven good and above anything proven bad. Zero
#: would make a freshly imported inventory look catastrophic; 100 would
#: make never looking the best possible outcome.
UNKNOWN_SCORE_CEILING = 50.0

#: v4 neighbourhood used when the announced prefix is coarser than this.
#: Two addresses in one /24 are almost always one rack.
V4_BLOCK_PREFIX = 24
#: v6 equivalent. A /64 is one LAN and a /48 is a typical site allocation;
#: /48 is the conservative choice for "same infrastructure".
V6_BLOCK_PREFIX = 48


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One named thing wrong with -- or worth knowing about -- a chain."""

    code: str
    severity: str
    title: str
    message: str
    positions: Tuple[int, ...] = ()
    detail: dict = field(default_factory=dict)

    @property
    def penalty(self):
        return PENALTIES.get(self.code, 0)

    def as_dict(self):
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "positions": list(self.positions),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HopInput:
    """What the analyser needs about one position.

    `host` is the lookup key; `provider` and `country` are what the
    operator recorded. Deliberately not a `Proxy`: the candidate-evaluation
    endpoint analyses hosts that are not in the inventory, and that is the
    use case the feature was asked for.
    """

    position: int
    host: str
    provider: str = ""
    country: str = ""
    proxy_id: Optional[int] = None
    label: str = ""


@dataclass(frozen=True)
class HopIntel:
    """One position, joined to whatever is known about it."""

    position: int
    host: str
    proxy_id: Optional[int]
    label: str
    provider: str
    asn: Optional[int]
    asn_org: str
    prefix: str
    block: str
    ip: Optional[str]
    country: str
    country_source: str
    operator_country: str
    registry: str
    source: str
    looked_up_at: object
    intel_known: bool
    intel_status: str

    @property
    def provider_key(self):
        return provider_key(self.provider)

    @property
    def org_key(self):
        """The AS operator's name, normalised for comparison.

        Same normalisation as a vendor label: registries write
        `ACME-HOSTING-LLC` where an operator would type `Acme Hosting
        LLC`, and two AS numbers belonging to one company often differ by
        nothing else. See `proxies.models.entity_key` for what this
        heuristic can and cannot do.
        """
        return entity_key(self.asn_org)

    def as_dict(self):
        return {
            "position": self.position,
            "host": self.host,
            "proxy": self.proxy_id,
            "label": self.label or None,
            "provider": self.provider or None,
            "asn": self.asn,
            "asn_org": self.asn_org or None,
            "prefix": self.prefix or None,
            "block": self.block or None,
            "ip": self.ip,
            "country": self.country or None,
            "country_source": self.country_source,
            "registry": self.registry or None,
            "intel_known": self.intel_known,
            "intel_status": self.intel_status,
            "intel_source": self.source or None,
            "looked_up_at": self.looked_up_at,
        }


@dataclass(frozen=True)
class DiversityReport:
    hops: List[HopIntel]
    findings: List[Finding]
    score: float
    vocabulary_note: str = VOCABULARY_NOTE

    @property
    def worst_severity(self):
        if not self.findings:
            return None
        return min(
            (finding.severity for finding in self.findings),
            key=lambda severity: SEVERITY_ORDER.get(severity, 99),
        )

    @property
    def known_hops(self):
        return sum(1 for hop in self.hops if hop.intel_known)

    @property
    def coverage(self):
        return (self.known_hops / len(self.hops)) if self.hops else 0.0

    def as_dict(self):
        return {
            "score": round(self.score, 1),
            "worst_severity": self.worst_severity,
            "summary": self.summary(),
            "vocabulary_note": self.vocabulary_note,
            "coverage": {
                "hops": len(self.hops),
                "known": self.known_hops,
                "unknown": len(self.hops) - self.known_hops,
                "fraction": round(self.coverage, 3),
                "score_ceiling": round(_coverage_ceiling(self.hops), 1),
            },
            "counts": self.counts(),
            "hops": [hop.as_dict() for hop in self.hops],
            "findings": [finding.as_dict() for finding in self.findings],
        }

    def counts(self):
        """Distinct values actually observed. Unknowns are excluded from
        every count rather than counted as one more distinct value --
        counting them would inflate exactly the number a user reads as
        'how spread out is this chain'."""
        return {
            "asns": len({hop.asn for hop in self.hops if hop.asn is not None}),
            "asn_orgs": len({hop.org_key for hop in self.hops if hop.org_key}),
            "providers": len({hop.provider_key for hop in self.hops if hop.provider_key}),
            "countries": len({hop.country for hop in self.hops if hop.country}),
            "networks": len({hop.block or hop.prefix for hop in self.hops if (hop.block or hop.prefix)}),
        }

    def summary(self):
        """One sentence, written for a user rather than an operator."""
        if not self.hops:
            return "There are no hops to analyse."
        if len(self.hops) == 1:
            return (
                "One hop is a proxy, not a chain: that operator sees both "
                "your address and your destination."
            )
        worst = self.worst_severity
        if worst == Severity.CRITICAL:
            return (
                "The first and last hops are related, so one party can see "
                "both ends of this chain."
            )
        if worst == Severity.UNKNOWN:
            return (
                "Some hops could not be checked, so this chain's diversity "
                "is unverified rather than confirmed."
            )
        if worst in (Severity.HIGH, Severity.MEDIUM):
            return "Some hops share a network, an operator or a vendor."
        if worst == Severity.LOW:
            return "The hops are on different networks but are concentrated in one jurisdiction."
        return "No shared network, operator or vendor was found between these hops."


# ---------------------------------------------------------------------------
# Building hop intel
# ---------------------------------------------------------------------------


def _block_for(ip_text, prefix_text):
    """The most specific network label available for an address.

    The announced prefix can be a /19 -- real, but a weak claim about
    shared infrastructure. A fixed /24 derived from the address is a
    stronger one. Whichever is more specific is the one reported, so a
    finding never understates what it found.
    """
    address = parse_ip(ip_text) if ip_text else None
    if address is None:
        return (prefix_text or "").strip()

    try:
        announced = ipaddress.ip_network(prefix_text, strict=False) if prefix_text else None
    except ValueError:
        announced = None

    width = V4_BLOCK_PREFIX if address.version == 4 else V6_BLOCK_PREFIX
    derived = ipaddress.ip_network(f"{address}/{width}", strict=False)

    if announced is None or announced.version != derived.version:
        return str(derived)
    return str(announced if announced.prefixlen >= derived.prefixlen else derived)


def hop_inputs_from_proxies(proxies):
    """`HopInput`s from an ordered sequence of `Proxy` rows."""
    return [
        HopInput(
            position=index,
            host=proxy.host,
            provider=proxy.provider,
            country=proxy.country,
            proxy_id=proxy.pk,
        )
        for index, proxy in enumerate(proxies)
    ]


#: Pseudonyms handed to end users in place of real vendor names.
PROVIDER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def pseudonymise_providers(entries: Sequence[HopInput]):
    """Replace real vendor names with "Vendor A", "Vendor B", ...

    The user needs to know that hops 1 and 3 came from **one** vendor.
    They do not need to know *which*, and telling them would publish the
    operator's supply chain to every subscriber -- which, given P4 (the
    provider's terms forbid redistribution), is evidence of the thing the
    terms forbid, handed out by our own API.

    Substituting at the input stage rather than scrubbing the output is
    deliberate: every finding message downstream is then generated from
    the pseudonym, so there is no code path that can reintroduce the real
    name by formatting a string the scrubber did not anticipate.

    The mapping is per analysis and ordered by first appearance, so it
    leaks nothing across chains -- "Vendor A" in one chain and "Vendor A"
    in another are not necessarily the same company, and the label is not
    stable enough to accumulate into a directory of the operator's
    suppliers.
    """
    mapping: Dict[str, str] = {}
    out = []
    for entry in entries:
        key = provider_key(entry.provider)
        if not key:
            out.append(entry)
            continue
        if key not in mapping:
            index = len(mapping)
            if index < len(PROVIDER_ALPHABET):
                label = f"Vendor {PROVIDER_ALPHABET[index]}"
            else:
                label = f"Vendor {index + 1}"
            mapping[key] = label
        out.append(
            HopInput(
                position=entry.position,
                host=entry.host,
                provider=mapping[key],
                country=entry.country,
                proxy_id=entry.proxy_id,
                label=entry.label,
            )
        )
    return out


def build_hop_intel(entries: Sequence[HopInput], records=None):
    """Join hops to cached intel. **Performs no lookups.**

    `records` is `{host_key: IpIntelRecord}`; when omitted it is read from
    the cache. Nothing here reaches the network, which is what makes this
    safe to call from a mobile read endpoint.
    """
    entries = list(entries)
    if records is None:
        records = cached_facts([entry.host for entry in entries])

    hops = []
    for entry in entries:
        key = normalise_host(entry.host)
        record = records.get(key)
        known = bool(record and record.is_known)
        operator_country = normalise_country(entry.country)
        intel_country = normalise_country(record.country) if record else ""

        if intel_country:
            country, country_source = intel_country, "intel"
        elif operator_country:
            # The vendor's label. Worth using -- it is the only data there
            # is -- but recorded as the vendor's claim, not as a
            # measurement, because that is what it is.
            country, country_source = operator_country, "operator"
        else:
            country, country_source = "", "unknown"

        hops.append(
            HopIntel(
                position=entry.position,
                host=entry.host,
                proxy_id=entry.proxy_id,
                label=entry.label,
                provider=entry.provider or "",
                asn=record.asn if known else None,
                asn_org=(record.asn_org if known else "") or "",
                prefix=(record.prefix if known else "") or "",
                block=_block_for(record.ip, record.prefix) if known else "",
                ip=(str(record.ip) if record and record.ip else None),
                country=country,
                country_source=country_source,
                operator_country=operator_country,
                registry=(record.registry if record else "") or "",
                source=(record.source if record else "") or "",
                looked_up_at=(record.looked_up_at if record else None),
                intel_known=known,
                intel_status=(record.status if record else "missing"),
            )
        )
    return hops


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _groups(hops, keyfunc):
    """`{key: [hop, ...]}` over hops with a truthy key, groups of 2+ only."""
    buckets: Dict[object, List[HopIntel]] = {}
    for hop in hops:
        key = keyfunc(hop)
        if key in (None, ""):
            continue
        buckets.setdefault(key, []).append(hop)
    return {key: group for key, group in buckets.items() if len(group) > 1}


def _positions(group):
    return tuple(sorted(hop.position for hop in group))


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _entry_exit_findings(hops):
    """Check 1, and the one that matters most.

    Only the most specific *network* relation is reported -- two hops in
    one /24 are obviously also on one ASN, and saying both would be two
    criticals for one fact. The vendor relation is reported separately
    because it is a different axis: same company, possibly different
    networks, and it is the one an ASN lookup cannot see.
    """
    if len(hops) < 2:
        return [], set()

    entry, exit_ = hops[0], hops[-1]
    findings = []
    positions = (entry.position, exit_.position)
    #: Which axes this function has already reported for the entry/exit
    #: pair. `_pair_findings` reads it so that one fact is not reported
    #: twice at two severities.
    reported = set()

    if entry.block and entry.block == exit_.block:
        reported.add("network")
        findings.append(
            Finding(
                code="entry_exit_shared_network",
                severity=Severity.CRITICAL,
                title="First and last hop are in the same network block",
                message=(
                    "Your first and last hop sit in the same network block "
                    f"({entry.block}). Whoever runs that block sees the "
                    "address you connected from and the destination you "
                    "connected to. The hops in between add latency and "
                    "nothing else."
                ),
                positions=positions,
                detail={
                    "network": entry.block,
                    "asn": entry.asn,
                    "asn_org": entry.asn_org or None,
                },
            )
        )
    elif entry.asn is not None and entry.asn == exit_.asn:
        reported.add("asn")
        findings.append(
            Finding(
                code="entry_exit_shared_asn",
                severity=Severity.CRITICAL,
                title="First and last hop are on the same ASN",
                message=(
                    f"Your first and last hop are both on AS{entry.asn}"
                    f"{(' (' + entry.asn_org + ')') if entry.asn_org else ''}. "
                    "One network operator therefore sees both the address "
                    "you connected from and the destination you connected "
                    "to, which is the correlation a chain exists to "
                    "prevent."
                ),
                positions=positions,
                detail={"asn": entry.asn, "asn_org": entry.asn_org or None},
            )
        )
    elif entry.org_key and entry.org_key == exit_.org_key:
        reported.add("org")
        findings.append(
            Finding(
                code="entry_exit_shared_asn_org",
                severity=Severity.CRITICAL,
                title="First and last hop belong to the same AS operator",
                message=(
                    f"Your first and last hop are on different AS numbers "
                    f"(AS{entry.asn} and AS{exit_.asn}) that are registered "
                    f"to the same operator, {entry.asn_org}. Different "
                    "networks, one company -- and that company sees both "
                    "ends of this chain."
                ),
                positions=positions,
                detail={
                    "asn_org": entry.asn_org,
                    "asns": [entry.asn, exit_.asn],
                },
            )
        )

    if entry.provider_key and entry.provider_key == exit_.provider_key:
        reported.add("provider")
        findings.append(
            Finding(
                code="entry_exit_shared_provider",
                severity=Severity.CRITICAL,
                title="First and last hop were bought from the same vendor",
                message=(
                    f"Your first and last hop were both bought from "
                    f"{entry.provider}. Even on different networks that is "
                    "one commercial relationship: one account, one payment "
                    "trail, one set of records, and one company that can be "
                    "asked about both ends of this chain at once."
                ),
                positions=positions,
                detail={
                    "provider": entry.provider,
                    "same_asn": entry.asn is not None and entry.asn == exit_.asn,
                },
            )
        )

    return findings, reported


def _pair_findings(hops, entry_exit_reported):
    """Checks 2-4 over every pair, reported as groups rather than pairs.

    A four-hop chain with all four on one ASN is one fact, not six. The
    group carries every position involved, which is what an operator needs
    to decide what to replace.

    Four suppression rules, all of them stopping one fact from being
    reported twice -- which would also charge the score for it twice,
    since the score is derived from the findings:

    * A group whose positions are exactly the entry/exit pair is dropped
      when `_entry_exit_findings` already reported that axis. The critical
      finding said it, more loudly. A group that is *wider* than the pair
      is kept, because it says something the critical finding did not.
    * An ASN group is dropped when a network group covers exactly the same
      positions, since the network claim is strictly more specific.
    * An AS-operator group is reported only when it spans more than one
      ASN; otherwise it is the ASN group wearing a different hat.
    * The vendor axis is checked independently of all of the above: it is
      the one relation routing data cannot see.
    """
    findings = []
    if len(hops) < 2:
        return findings

    entry_exit_pair = tuple(sorted((hops[0].position, hops[-1].position)))
    network_positions = set()

    for block, group in sorted(_groups(hops, lambda hop: hop.block).items()):
        positions = _positions(group)
        network_positions.add(positions)
        if positions == entry_exit_pair and "network" in entry_exit_reported:
            continue
        findings.append(
            Finding(
                code="shared_network",
                severity=Severity.HIGH,
                title="Hops share a network block",
                message=(
                    f"Hops {_join(positions)} are all inside {block}. "
                    "Addresses that close together are very likely the same "
                    "rack, often the same machine -- treat them as one hop, "
                    "not several."
                ),
                positions=positions,
                detail={
                    "network": block,
                    "asn": group[0].asn,
                    "asn_org": group[0].asn_org or None,
                },
            )
        )

    for asn, group in sorted(_groups(hops, lambda hop: hop.asn).items()):
        positions = _positions(group)
        if positions in network_positions:
            continue
        if positions == entry_exit_pair and entry_exit_reported & {"asn", "network"}:
            continue
        org = group[0].asn_org
        findings.append(
            Finding(
                code="shared_asn",
                severity=Severity.MEDIUM,
                title="Hops share an ASN",
                message=(
                    f"Hops {_join(positions)} are all on AS{asn}"
                    f"{(' (' + org + ')') if org else ''}. One network "
                    "operator can see the traffic entering and leaving each "
                    "of them."
                ),
                positions=positions,
                detail={"asn": asn, "asn_org": org or None},
            )
        )

    for org_key, group in sorted(_groups(hops, lambda hop: hop.org_key).items()):
        asns = {hop.asn for hop in group if hop.asn is not None}
        if len(asns) < 2:
            continue  # same ASN: already reported above
        positions = _positions(group)
        if positions == entry_exit_pair and "org" in entry_exit_reported:
            continue
        findings.append(
            Finding(
                code="shared_asn_org",
                severity=Severity.MEDIUM,
                title="Hops are on different ASNs owned by one operator",
                message=(
                    f"Hops {_join(positions)} are on different AS numbers "
                    f"({_join(sorted(asns), prefix='AS')}) that are all "
                    f"registered to {group[0].asn_org}. Separate networks, "
                    "one company."
                ),
                positions=positions,
                detail={"asn_org": group[0].asn_org, "asns": sorted(asns)},
            )
        )

    for _key, group in sorted(
        _groups(hops, lambda hop: hop.provider_key).items()
    ):
        positions = _positions(group)
        if positions == entry_exit_pair and "provider" in entry_exit_reported:
            # The critical entry/exit finding already said this, more
            # loudly. Reporting it again at medium would also charge the
            # score twice for one fact.
            continue
        asns = {hop.asn for hop in group if hop.asn is not None}
        findings.append(
            Finding(
                code="shared_provider",
                severity=Severity.MEDIUM,
                title="Hops were bought from the same vendor",
                message=(
                    f"Hops {_join(positions)} were all bought from "
                    f"{group[0].provider}"
                    + (
                        f", on {len(asns)} different networks"
                        if len(asns) > 1
                        else ""
                    )
                    + ". Different ASNs do not make these independent: it is "
                    "one account, one invoice and one company's records."
                ),
                positions=positions,
                detail={
                    "provider": group[0].provider,
                    "asns": sorted(asns),
                    "distinct_asns": len(asns),
                },
            )
        )

    return findings


def _jurisdiction_findings(hops):
    """Check 5. Silent when any hop's country is unknown.

    A concentration claim about hops whose location is unknown would be a
    guess, and a guess that happens to be reassuring ("not all in one
    country!") is the worst kind. The unknown hops are already reported by
    `_unknown_findings`.
    """
    if len(hops) < 2:
        return []

    countries = [hop.country for hop in hops]
    if not all(countries):
        return []

    findings = []
    distinct = sorted(set(countries))
    operator_sourced = [hop.position for hop in hops if hop.country_source == "operator"]

    if len(distinct) == 1:
        findings.append(
            Finding(
                code="jurisdiction_single_country",
                severity=Severity.MEDIUM,
                title="Every hop is in one country",
                message=(
                    f"All {len(hops)} hops are in {distinct[0]}. One legal "
                    "system covers the whole chain, so a single lawful "
                    "request can reach every operator in it."
                ),
                positions=tuple(hop.position for hop in hops),
                detail={
                    "country": distinct[0],
                    "operator_reported_positions": operator_sourced,
                },
            )
        )
        return findings

    bloc = smallest_bloc_containing(distinct)
    if bloc is not None:
        findings.append(
            Finding(
                code="jurisdiction_single_bloc",
                severity=Severity.LOW,
                title=f"Every hop is inside the {bloc['label']}",
                message=(
                    f"The hops are in {', '.join(distinct)} -- different "
                    f"countries, but all within the {bloc['label']}, whose "
                    "members have documented arrangements for sharing "
                    "exactly this kind of record. Which groupings matter is "
                    "a judgement call; this one comes from the configured "
                    "bloc map and can be changed."
                ),
                positions=tuple(hop.position for hop in hops),
                detail={
                    "bloc": bloc["key"],
                    "label": bloc["label"],
                    "countries": distinct,
                    "operator_reported_positions": operator_sourced,
                },
            )
        )
    return findings


def _unknown_findings(hops):
    """Check 7, and the reason the rest of the module can be trusted.

    Two separate statements, because they have different remedies: a hop
    with no ASN lookup needs `refresh_ip_intel`, while a hop with no vendor
    recorded needs an operator to type one in.
    """
    findings = []

    unknown = [hop for hop in hops if not hop.intel_known]
    if unknown:
        statuses = sorted({hop.intel_status for hop in unknown})
        findings.append(
            Finding(
                code="hop_intel_unknown",
                severity=Severity.UNKNOWN,
                title="Some hops have no network information",
                message=(
                    f"{len(unknown)} of {len(hops)} hops have no ASN lookup "
                    "yet, so nothing below has been checked for them. This "
                    "is not a pass: an unchecked hop may well be on the "
                    "same network as another one. Run `refresh_ip_intel` "
                    "for these hosts."
                ),
                positions=tuple(hop.position for hop in unknown),
                detail={
                    "hosts": [hop.host for hop in unknown],
                    "statuses": statuses,
                    "known": len(hops) - len(unknown),
                    "total": len(hops),
                },
            )
        )

    unlabelled = [hop for hop in hops if not hop.provider_key]
    if unlabelled and len(hops) > 1:
        findings.append(
            Finding(
                code="provider_unrecorded",
                severity=Severity.UNKNOWN,
                title="Some hops have no vendor recorded",
                message=(
                    f"{len(unlabelled)} of {len(hops)} hops have no vendor "
                    "recorded. Two blanks are not two different vendors -- "
                    "the vendor check simply could not run for these hops, "
                    "and two of them may well have come from the same "
                    "reseller."
                ),
                positions=tuple(hop.position for hop in unlabelled),
                detail={
                    "hosts": [hop.host for hop in unlabelled],
                    "labelled": len(hops) - len(unlabelled),
                    "total": len(hops),
                },
            )
        )

    return findings


def _shape_findings(hops):
    """Check 6, plus the empty case."""
    if not hops:
        return [
            Finding(
                code="empty_chain",
                severity=Severity.INFO,
                title="Nothing to analyse",
                message="This chain has no hops.",
            )
        ]
    if len(hops) == 1:
        return [
            Finding(
                code="single_hop",
                severity=Severity.INFO,
                title="This is a proxy, not a chain",
                message=(
                    "A single hop gives no correlation resistance: that one "
                    "operator sees the address you connected from and the "
                    "destination you connected to. That is the same "
                    "exposure the first-and-last-hop check exists to catch, "
                    "stated here as a fact about the shape you chose rather "
                    "than as a fault in the proxy. Add a second hop from a "
                    "different operator to get anything from chaining."
                ),
                positions=(hops[0].position,),
            )
        ]
    return []


def _join(values, prefix=""):
    values = [f"{prefix}{value}" for value in values]
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " and " + values[-1]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _coverage_ceiling(hops):
    """The highest score a chain with this much missing data may reach.

    Linear between `UNKNOWN_SCORE_CEILING` (nothing known) and 100
    (everything known). This is what makes "unknown is not a pass"
    structural rather than a comment: no amount of clean findings can lift
    a chain nobody has looked up above the ceiling, because the findings
    were computed from data that does not exist.
    """
    if not hops:
        return 0.0
    known = sum(1 for hop in hops if hop.intel_known)
    coverage = known / len(hops)
    return UNKNOWN_SCORE_CEILING + (100.0 - UNKNOWN_SCORE_CEILING) * coverage


def score_findings(hops, findings):
    """0-100 correlation resistance, derived from the findings.

    A single hop scores 0 regardless of how clean its data is. That is not
    a verdict on the proxy -- it is the definition: correlation resistance
    between entry and exit is what is being measured, and with one hop
    there is only one party and it is both.
    """
    if not hops:
        return 0.0
    if len(hops) == 1:
        return 0.0

    score = 100.0 - sum(finding.penalty for finding in findings)
    return max(0.0, min(score, _coverage_ceiling(hops)))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def analyse_hops(entries: Sequence[HopInput], *, records=None) -> DiversityReport:
    """Analyse an ordered list of hops. **No network, no lookups.**"""
    hops = build_hop_intel(entries, records=records)

    findings = list(_shape_findings(hops))
    if len(hops) >= 2:
        entry_exit, reported = _entry_exit_findings(hops)
        findings.extend(entry_exit)
        findings.extend(_pair_findings(hops, reported))
        findings.extend(_jurisdiction_findings(hops))
    findings.extend(_unknown_findings(hops))

    findings.sort(
        key=lambda finding: (
            SEVERITY_ORDER.get(finding.severity, 99),
            finding.code,
            finding.positions,
        )
    )
    return DiversityReport(hops=hops, findings=findings, score=score_findings(hops, findings))


def analyse_proxies(proxies, *, records=None, transform=None) -> DiversityReport:
    """Analyse an ordered sequence of `Proxy` rows.

    `transform` rewrites the hop inputs before anything is computed --
    `pseudonymise_providers` is the one that matters, and applying it here
    rather than to the output is what makes a real vendor name
    unreachable from a response rather than merely absent from one.
    """
    entries = hop_inputs_from_proxies(proxies)
    if transform is not None:
        entries = transform(entries)
    return analyse_hops(entries, records=records)


def analyse_chain(chain, *, resolved_proxies=None, records=None, transform=None):
    """Analyse a saved `ProxyChain`.

    `resolved_proxies` is the output of `proxies.resolution` when the
    caller could resolve the chain -- that is what will actually be
    connected, so it is what should be analysed. Without it the fixed hops
    are analysed and every constrained position is reported as unresolved
    and unknown, which is honest: a slot that has not picked a proxy has no
    ASN to check.
    """
    hops = list(chain.hops.all().order_by("position"))

    if resolved_proxies is not None:
        return analyse_proxies(
            resolved_proxies, records=records, transform=transform
        )

    entries = []
    unresolved = []
    for index, hop in enumerate(hops):
        if hop.proxy_id and hop.proxy is not None:
            entries.append(
                HopInput(
                    position=index,
                    host=hop.proxy.host,
                    provider=hop.proxy.provider,
                    country=hop.proxy.country,
                    proxy_id=hop.proxy.pk,
                )
            )
        else:
            unresolved.append(index)
            entries.append(
                HopInput(
                    position=index,
                    host="",
                    label=hop.describe_constraints(),
                )
            )

    if transform is not None:
        entries = transform(entries)
    report = analyse_hops(entries, records=records)
    if not unresolved:
        return report

    findings = list(report.findings)
    findings.append(
        Finding(
            code="hop_unresolved",
            severity=Severity.UNKNOWN,
            title="Some positions have not picked a proxy yet",
            message=(
                f"{len(unresolved)} position(s) in this chain are "
                "constraints rather than fixed proxies, and could not be "
                "resolved right now. Nothing below has been checked for "
                "them -- the proxy that fills the slot at connect time may "
                "share a network with another hop."
            ),
            positions=tuple(unresolved),
            detail={"positions": unresolved},
        )
    )
    findings.sort(
        key=lambda finding: (
            SEVERITY_ORDER.get(finding.severity, 99),
            finding.code,
            finding.positions,
        )
    )
    return DiversityReport(
        hops=report.hops,
        findings=findings,
        score=score_findings(report.hops, findings),
    )
