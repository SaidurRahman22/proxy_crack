"""Turning a chain definition into concrete proxies, at connect time.

This is the feature proxychains structurally cannot have. There, a hop is a
line in a config file naming one host; when that host dies the chain dies,
and you edit the file. Here a hop can instead be a *constraint* -- "position
2 = any premium SOCKS5 in NL, p95 under 200 ms" -- and the concrete proxy
is chosen when the tunnel is built, from live health data.

Four rules the resolver enforces, in this order:

1. **Entitlement first, and only from the entitlement resolver.** The
   candidate queryset is scoped by `billing.entitlements.resolve_entitlement`
   before any constraint is applied, so a constraint can narrow the result
   and can never widen it. A hop asking for `tier=premium` on a free plan
   does not get a premium proxy; it gets a clean "no candidate" error.
2. **No proxy twice in one chain.** A chain that resolves to the same proxy
   at positions 1 and 3 is not a chain -- that operator sees both ends, and
   the user believes they bought two hops. Fixed hops are reserved *before*
   any constrained hop picks, so a constrained slot cannot steal the proxy a
   later fixed slot needs.
3. **Hardest slot first.** Constrained hops are assigned in order of how few
   candidates they have, not in position order. With one NL proxy and two NL
   hops where only one of them also demands SOCKS5, position order can take
   the SOCKS5 proxy for the unconstrained slot and then fail the constrained
   one, for a chain that was satisfiable. See `_assignment_order`.
4. **Fail loudly, with the reason.** An unsatisfiable slot reports which
   position failed, what it asked for, and the candidate count after each
   filter stage -- "12 in NL, 9 enabled, 4 alive, 0 with success_rate >=
   0.95". Knowing *which* clause emptied the set is the difference between
   fixing a chain and guessing at it.

   The health stage splits that count two ways, because "measured and too
   slow" and "never measured" empty the set identically and are fixed by
   opposite actions -- replace the proxy versus run a probe. A hop that
   fails *only* because nothing has been measured gets its own error code
   (`hop_unsatisfied_unprobed`) naming the command to run. On a freshly
   imported inventory that is every hop with a latency or success-rate
   constraint, and reading it as "no proxy is good enough" would send an
   operator hunting for a bug that is not there. See `_unsatisfied`.

   `ChainHop.allow_unprobed` is the way through without deleting the
   constraint: off by default, because a guarantee nothing has measured
   is not a guarantee.

Known limitation, deliberately not solved here: rule 3 is a heuristic, not
an optimal bipartite matching. A pathological chain can still fail where a
different assignment would have succeeded. Hopcroft-Karp over the
hop/candidate graph is the correct answer and slots in at
`_assign_constrained` without touching anything else; at the current ceiling
of 10 hops it is not yet worth the code.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from django.db.models import F, Q

from billing.entitlements import resolve_entitlement
from proxies.models import ChainHop, Proxy

__all__ = [
    "ChainResolution",
    "ChainResolutionError",
    "ResolvedHop",
    "candidate_queryset",
    "resolve_chain",
    "resolve_hops",
]


class ChainResolutionError(Exception):
    """A chain that cannot be turned into concrete proxies right now.

    Carries the failing position and a machine-readable code so the client
    can say "hop 2 has no healthy candidate" rather than "error".
    """

    def __init__(self, code, message, *, position=None, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.position = position
        self.detail = detail or {}

    def as_detail(self):
        payload = {"message": self.message, "code": self.code}
        if self.position is not None:
            payload["position"] = self.position
        payload.update(self.detail)
        return payload


@dataclass(frozen=True)
class ResolvedHop:
    """One position, resolved to exactly one proxy."""

    position: int
    hop_id: int
    proxy: Proxy
    fixed: bool
    constraints: dict = field(default_factory=dict)
    candidate_count: int = 1

    @property
    def is_failover_capable(self):
        """Whether this slot could re-resolve if its proxy died.

        A fixed hop cannot: that is the whole point of the distinction, and
        it is worth surfacing to the client so a chain builder can show
        which positions are single points of failure.
        """
        return not self.fixed and self.candidate_count > 1


@dataclass(frozen=True)
class ChainResolution:
    hops: List[ResolvedHop]
    entitlement: object

    @property
    def proxy_ids(self):
        return [hop.proxy.pk for hop in self.hops]


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def health_threshold_q(hop, *, allow_unprobed):
    """The `max_latency_ms` / `min_success_rate` clause, as a `Q`.

    Built as one `Q` rather than two chained `.filter()` calls because
    `allow_unprobed` has to OR across *both* thresholds at once. Chaining
    `.filter(p95 <= x | unprobed).filter(rate >= y | unprobed)` is subtly
    different and not what anyone means: it would admit a proxy that is
    unprobed for one clause and measured-but-failing for the other, which
    cannot happen today but would the moment a third threshold appears.

    "Unprobed" is `probe_count = 0`, matching `Proxy.is_probed`, rather
    than `latency_p95_ms IS NULL`. A proxy probed fifty times that failed
    every one has a null p95 too, and it is emphatically not unmeasured --
    it is measured and bad. Keying on the null would quietly re-admit the
    worst proxies in the inventory under a flag whose entire promise is
    "nothing is known about these yet".
    """
    clause = Q()
    if hop.max_latency_ms is not None:
        clause &= Q(latency_p95_ms__lte=hop.max_latency_ms)
    if hop.min_success_rate is not None:
        clause &= Q(success_rate__gte=hop.min_success_rate)
    if not clause:
        return Q()
    if allow_unprobed:
        return clause | Q(probe_count=0)
    return clause


def candidate_queryset(hop, entitlement, exclude_ids=(), *, allow_unprobed=None):
    """Healthy, entitled, constraint-matching proxies, best first.

    Ordering is `-quality_score` then p95 then pk. The score already folds
    reliability and latency together with reliability dominant (see
    `proxies.health`), so this is "healthiest candidate" in the sense the
    roadmap asks for -- not "fastest", which is how you end up with a chain
    built from quick proxies that drop a third of their connections.

    `pk` is the final tie-break purely so the choice is deterministic;
    without it two identical proxies would swap places between calls and
    nothing would be reproducible.

    `allow_unprobed` overrides `hop.allow_unprobed` and exists for one
    caller: the failure path, which re-runs the selection with the flag
    forced on to find out whether "never measured" was the *only* thing
    standing in the way. Passing `None` -- every normal call -- uses what
    the hop actually says.
    """
    if allow_unprobed is None:
        allow_unprobed = hop.allow_unprobed
    queryset = Proxy.objects.filter(
        is_enabled=True,
        is_alive=True,
        tier__in=entitlement.allowed_proxy_tiers,
    )
    if exclude_ids:
        queryset = queryset.exclude(pk__in=list(exclude_ids))

    if hop.protocol:
        queryset = queryset.filter(protocol=hop.protocol)
    if hop.country:
        queryset = queryset.filter(country__iexact=hop.country)
    if hop.tier:
        # Intersected with the entitlement scope above, never replacing it.
        queryset = queryset.filter(tier=hop.tier)

    # p95, not the mean and not `latency_ms`. An unprobed proxy has a null
    # p95 and is excluded by this comparison, which is correct by default:
    # the hop asked for a latency guarantee and there is no measurement to
    # make it with. `allow_unprobed` is how an operator says "I know, let
    # them through anyway" -- see P15 and `health_threshold_q`.
    thresholds = health_threshold_q(hop, allow_unprobed=allow_unprobed)
    if thresholds:
        queryset = queryset.filter(thresholds)

    return queryset.order_by(
        F("quality_score").desc(),
        F("latency_p95_ms").asc(nulls_last=True),
        "pk",
    )


# Stage order matters: it is read by a human diagnosing an empty result, so
# it runs widest to narrowest.
_DIAGNOSTIC_STAGES = (
    ("enabled", lambda qs, hop, ent: qs.filter(is_enabled=True)),
    ("alive", lambda qs, hop, ent: qs.filter(is_alive=True)),
    (
        "entitled",
        lambda qs, hop, ent: qs.filter(tier__in=ent.allowed_proxy_tiers),
    ),
    (
        "protocol",
        lambda qs, hop, ent: qs.filter(protocol=hop.protocol) if hop.protocol else qs,
    ),
    (
        "country",
        lambda qs, hop, ent: qs.filter(country__iexact=hop.country)
        if hop.country
        else qs,
    ),
    ("tier", lambda qs, hop, ent: qs.filter(tier=hop.tier) if hop.tier else qs),
)

# The health thresholds, diagnosed one field at a time so a reader can see
# which of the two emptied the set. Kept as separate stages (rather than
# one combined "health" stage) because the stage names are what the API
# reports and what an operator reads.
#
# Applying them one at a time is equivalent to the single OR-ed clause
# `candidate_queryset` uses, because "unprobed" is one predicate on the
# row (`probe_count = 0`) and not a per-field null test: an unprobed proxy
# satisfies the escape in every stage, a measured one in none. If
# "unprobed" ever became per-field -- `latency_p95_ms IS NULL`, say -- the
# two forms would diverge and this would have to become one stage.
_HEALTH_STAGES = (
    (
        "max_latency_ms",
        lambda hop: Q(latency_p95_ms__lte=hop.max_latency_ms)
        if hop.max_latency_ms is not None
        else None,
    ),
    (
        "min_success_rate",
        lambda hop: Q(success_rate__gte=hop.min_success_rate)
        if hop.min_success_rate is not None
        else None,
    ),
)


def diagnose(hop, entitlement, exclude_ids=()):
    """Candidate count after each filter stage.

    Costs a few COUNTs per stage and only ever runs on the failure path,
    where the request is already lost and an operator needs an answer more
    than the database needs the query budget back.

    The two health stages are reported **split**, because each has two
    ways of excluding a proxy whose remedies are opposite, and a single
    "0 remaining" cannot tell them apart (P15):

        {"stage": "max_latency_ms", "remaining": 0,
         "excluded_measured": 3,   -> measured, and genuinely too slow:
                                      replace these proxies
         "excluded_unprobed": 12}  -> never measured at all:
                                      run `probe_proxies`

    On a freshly imported inventory the second number is the whole
    candidate pool and the first is zero, which is the case that reads as
    a bug and is not one.
    """
    queryset = Proxy.objects.all()
    stages = [{"stage": "all_proxies", "remaining": queryset.count()}]
    if exclude_ids:
        queryset = queryset.exclude(pk__in=list(exclude_ids))
        stages.append({"stage": "not_already_used", "remaining": queryset.count()})

    for name, apply in _DIAGNOSTIC_STAGES:
        queryset = apply(queryset, hop, entitlement)
        stages.append({"stage": name, "remaining": queryset.count()})

    for name, build in _HEALTH_STAGES:
        stage, queryset = _diagnose_health(queryset, hop, name, build(hop))
        stages.append(stage)

    return stages


def _diagnose_health(entering, hop, name, clause):
    """One health stage, with its exclusion split by cause.

    Returns `(stage_dict, surviving_queryset)` so the caller threads the
    narrowing queryset through exactly as the non-health stages do.
    """
    if clause is None:
        return (
            {
                "stage": name,
                "remaining": entering.count(),
                "excluded_measured": 0,
                "excluded_unprobed": 0,
                "applied": False,
            },
            entering,
        )

    unprobed_in = entering.filter(probe_count=0).count()
    measured_in = entering.count() - unprobed_in

    if hop.allow_unprobed:
        clause = clause | Q(probe_count=0)
    surviving = entering.filter(clause)

    remaining = surviving.count()
    # With the opt-in on, unprobed proxies survive this stage; count how
    # many so the two "excluded" figures stay exact rather than
    # approximate.
    surviving_unprobed = (
        surviving.filter(probe_count=0).count() if hop.allow_unprobed else 0
    )

    return (
        {
            "stage": name,
            "remaining": remaining,
            # Measured, and the measurement did not meet the bar. The
            # remedy is a better proxy; probing more will not help.
            "excluded_measured": measured_in - (remaining - surviving_unprobed),
            # Never measured, so there is nothing to compare against. The
            # remedy is a probe run, and these proxies may well be fine.
            "excluded_unprobed": unprobed_in - surviving_unprobed,
            "applied": True,
            "allow_unprobed": hop.allow_unprobed,
        },
        surviving,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_chain(chain, *, user=None, entitlement=None):
    """Resolve a saved `ProxyChain`. See `resolve_hops`."""
    hops = list(chain.hops.select_related("proxy").order_by("position"))
    return resolve_hops(hops, user=user or chain.user, entitlement=entitlement)


def resolve_hops(hops: Sequence[ChainHop], *, user=None, entitlement=None):
    """Resolve an ordered list of hops into concrete proxies.

    Takes hops rather than a chain so an unsaved chain -- a preview in the
    builder -- resolves through exactly the same code as a saved one. Two
    resolvers would drift, and the one that drifted would be the one the
    user sees before they commit.
    """
    entitlement = entitlement or resolve_entitlement(user)

    if not hops:
        raise ChainResolutionError("chain_empty", "A chain needs at least one hop.")

    if not entitlement.allows_chain_length(len(hops)):
        # Reachable without any edit by the user: a subscription expiring
        # shrinks `max_chain_length` under a chain that was legal when it
        # was saved.
        raise ChainResolutionError(
            "chain_length_exceeded",
            "This chain is longer than your plan allows.",
            detail={
                "requested_length": len(hops),
                "max_chain_length": entitlement.max_chain_length,
                "plan": entitlement.plan_code,
            },
        )

    resolved: Dict[int, ResolvedHop] = {}
    used: List[int] = []

    # Pass 1 -- fixed hops. Reserved before anything is chosen, so a
    # constrained slot cannot take a proxy a later fixed slot requires.
    for hop in hops:
        if hop.is_constrained:
            continue
        _check_fixed(hop, entitlement, used)
        used.append(hop.proxy_id)
        resolved[hop.position] = ResolvedHop(
            position=hop.position,
            hop_id=hop.pk,
            proxy=hop.proxy,
            fixed=True,
        )

    # Pass 2 -- constrained hops, hardest first.
    _assign_constrained(
        [hop for hop in hops if hop.is_constrained], entitlement, used, resolved
    )

    return ChainResolution(
        hops=[resolved[hop.position] for hop in hops], entitlement=entitlement
    )


def _check_fixed(hop, entitlement, used):
    proxy = hop.proxy
    if proxy.pk in used:
        raise ChainResolutionError(
            "hop_duplicate_proxy",
            "The same proxy appears at more than one position in this chain.",
            position=hop.position,
            detail={"proxy_id": proxy.pk},
        )
    if not proxy.is_enabled:
        raise ChainResolutionError(
            "hop_proxy_disabled",
            "This hop is pinned to a proxy the operator has disabled.",
            position=hop.position,
            detail={"proxy_id": proxy.pk},
        )
    if not entitlement.allows_tier(proxy.tier):
        # Deliberately does not name the tier -- same reasoning as
        # api.serializers.ProxyChainSerializer.
        raise ChainResolutionError(
            "hop_proxy_not_entitled",
            "This hop is pinned to a proxy your plan does not include.",
            position=hop.position,
            detail={
                "proxy_id": proxy.pk,
                "allowed_proxy_tiers": list(entitlement.allowed_proxy_tiers),
                "plan": entitlement.plan_code,
            },
        )
    if not proxy.is_alive:
        raise ChainResolutionError(
            "hop_proxy_dead",
            "This hop is pinned to a proxy that is failing health checks.",
            position=hop.position,
            detail={
                "proxy_id": proxy.pk,
                "consecutive_failures": proxy.consecutive_failures,
                "last_checked": proxy.last_checked,
                # The actionable part: a fixed hop has nothing to fail over
                # to. This is the sentence that sells the feature.
                "hint": (
                    "A fixed hop cannot fail over. Replace it with a "
                    "constrained hop to let this position re-resolve."
                ),
            },
        )


def _unsatisfied(hop, entitlement, used):
    """Build the right failure for an empty slot.

    Two different errors, because they have two different fixes and
    conflating them is the P15 complaint: a hop that excluded everything
    on a latency guarantee looks identical to one with no inventory at
    all, and on a freshly imported set the honest answer is "nothing has
    been measured yet", not "no proxy is good enough".

    The distinguishing question is asked directly rather than inferred:
    re-run the same selection with `allow_unprobed` forced on. Since the
    strict pass returned nothing, anything that appears now appeared
    *because* it is unprobed -- so a non-zero count here means the slot is
    satisfiable the moment somebody probes.
    """
    diagnosis = diagnose(hop, entitlement, used)
    shared = {
        "constraints": hop.constraint_dict(),
        "already_used_proxy_ids": list(used),
        "candidates": diagnosis,
    }

    if hop.has_health_thresholds and not hop.allow_unprobed:
        unprobed = candidate_queryset(
            hop, entitlement, used, allow_unprobed=True
        ).count()
        if unprobed:
            return ChainResolutionError(
                "hop_unsatisfied_unprobed",
                f"No proxy satisfies this hop yet: {unprobed} "
                f"{'candidate matches' if unprobed == 1 else 'candidates match'} "
                f"everything else it asks for, but "
                f"{'has' if unprobed == 1 else 'have'} never been probed, "
                f"so there is no measurement to check against. Run "
                f"`probe_proxies` to measure them, or set "
                f"`allow_unprobed` on this hop to accept them as they are.",
                position=hop.position,
                detail={
                    **shared,
                    "unprobed_candidates": unprobed,
                    # Spelled out so a client can render a button rather
                    # than parse the sentence above.
                    "remedy": {
                        "command": "probe_proxies",
                        "hint": (
                            f"{unprobed} candidates have never been "
                            f"probed; run `probe_proxies`"
                        ),
                        "override_field": "allow_unprobed",
                    },
                },
            )

    return ChainResolutionError(
        "hop_unsatisfied",
        "No available proxy satisfies this hop.",
        position=hop.position,
        detail=shared,
    )


def _assignment_order(hops, entitlement, used):
    """Constrained hops, fewest candidates first. See rule 3 up top."""
    counted = [
        (candidate_queryset(hop, entitlement, used).count(), hop.position, hop)
        for hop in hops
    ]
    counted.sort(key=lambda row: (row[0], row[1]))
    return counted


def _assign_constrained(hops, entitlement, used, resolved):
    if not hops:
        return
    for _initial_count, _position, hop in _assignment_order(hops, entitlement, used):
        queryset = candidate_queryset(hop, entitlement, used)
        # Counted exactly rather than inferred from a LIMIT: the number is
        # reported to the client as "this slot has N alternatives", and a
        # capped figure there would be a quiet lie.
        candidate_count = queryset.count()
        if not candidate_count:
            raise _unsatisfied(hop, entitlement, used)
        chosen = queryset.first()
        used.append(chosen.pk)
        resolved[hop.position] = ResolvedHop(
            position=hop.position,
            hop_id=hop.pk,
            proxy=chosen,
            fixed=False,
            constraints=hop.constraint_dict(),
            candidate_count=candidate_count,
        )
