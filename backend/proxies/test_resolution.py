"""Positional failover: turning constrained hops into concrete proxies.

The feature proxychains structurally cannot have. These tests pin the four
rules in `proxies.resolution`: entitlement first, no proxy twice, hardest
slot first, and fail loudly with the reason.

No prober and no network -- health is written straight onto the proxy rows
here, because what is under test is the *selection*, not the measurement.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.entitlements import resolve_entitlement
from billing.models import Plan, Subscription
from proxies.models import (
    ChainHop,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
)
from proxies.resolution import (
    ChainResolutionError,
    candidate_queryset,
    resolve_chain,
    resolve_hops,
)

User = get_user_model()


def make_proxy(host, **kwargs):
    """A proxy with explicit health. Defaults to a good one."""
    defaults = {
        "protocol": ProxyProtocol.SOCKS5,
        "port": 1080,
        "tier": ProxyTier.PREMIUM,
        "country": "NL",
        "is_alive": True,
        "is_enabled": True,
        "success_rate": 1.0,
        "probe_count": 50,
        "latency_p50_ms": 80,
        "latency_p95_ms": 100,
        "quality_score": 90.0,
        "last_checked": timezone.now(),
    }
    defaults.update(kwargs)
    return Proxy.objects.create(host=host, **defaults)


class ResolutionTestCase(TestCase):
    def setUp(self):
        self.free_plan = Plan.objects.create(
            code="free",
            name="Free",
            max_chain_length=1,
            max_proxy_tier=ProxyTier.FREE,
            is_free_tier=True,
            duration_days=0,
        )
        self.pro_plan = Plan.objects.create(
            code="pro",
            name="Pro",
            max_chain_length=4,
            max_proxy_tier=ProxyTier.PREMIUM,
            duration_days=30,
            sort_order=1,
        )
        self.free_user = User.objects.create_user("free@example.com", "Str0ngPass!phrase")
        self.paid_user = User.objects.create_user("paid@example.com", "Str0ngPass!phrase")
        Subscription.objects.create(user=self.paid_user, plan=self.pro_plan)

    def chain(self, user, *hop_kwargs, name="route"):
        """Build a saved chain. Each argument is one hop's kwargs."""
        chain = ProxyChain.objects.create(user=user, name=name)
        for position, kwargs in enumerate(hop_kwargs):
            ChainHop.objects.create(chain=chain, position=position, **kwargs)
        return chain


# ---------------------------------------------------------------------------
# Picking the healthiest candidate
# ---------------------------------------------------------------------------


class HealthiestCandidateTests(ResolutionTestCase):
    def test_a_constrained_hop_picks_the_highest_scoring_candidate(self):
        make_proxy("203.0.113.1", quality_score=40.0)
        best = make_proxy("203.0.113.2", quality_score=95.0)
        make_proxy("203.0.113.3", quality_score=70.0)

        chain = self.chain(self.paid_user, {"country": "NL"})
        resolution = resolve_chain(chain)

        self.assertEqual(resolution.proxy_ids, [best.pk])
        self.assertFalse(resolution.hops[0].fixed)
        self.assertEqual(resolution.hops[0].candidate_count, 3)
        self.assertTrue(resolution.hops[0].is_failover_capable)

    def test_reliability_beats_latency_end_to_end(self):
        """The ranking the roadmap asks for, exercised through the
        resolver rather than through the scoring function."""
        from proxies.health import compute_quality_score

        lossy_fast = make_proxy(
            "203.0.113.10",
            success_rate=0.70,
            probe_count=50,
            latency_p95_ms=50,
            quality_score=compute_quality_score(0.70, 50, 50),
        )
        reliable_slower = make_proxy(
            "203.0.113.11",
            success_rate=1.0,
            probe_count=50,
            latency_p95_ms=180,
            quality_score=compute_quality_score(1.0, 50, 180),
        )
        chain = self.chain(self.paid_user, {})
        resolution = resolve_chain(chain)
        self.assertEqual(resolution.proxy_ids, [reliable_slower.pk])
        self.assertNotEqual(resolution.proxy_ids, [lossy_fast.pk])

    def test_dead_proxies_are_never_selected(self):
        make_proxy("203.0.113.20", quality_score=99.0, is_alive=False)
        alive = make_proxy("203.0.113.21", quality_score=10.0)
        chain = self.chain(self.paid_user, {})
        self.assertEqual(resolve_chain(chain).proxy_ids, [alive.pk])

    def test_disabled_proxies_are_never_selected(self):
        make_proxy("203.0.113.30", quality_score=99.0, is_enabled=False)
        enabled = make_proxy("203.0.113.31", quality_score=10.0)
        chain = self.chain(self.paid_user, {})
        self.assertEqual(resolve_chain(chain).proxy_ids, [enabled.pk])

    def test_selection_is_deterministic_for_identical_candidates(self):
        first = make_proxy("203.0.113.40", quality_score=80.0)
        make_proxy("203.0.113.41", quality_score=80.0)
        chain = self.chain(self.paid_user, {})
        for _ in range(3):
            self.assertEqual(resolve_chain(chain).proxy_ids, [first.pk])

    def test_constraints_are_all_applied(self):
        make_proxy("203.0.113.50", country="DE")
        make_proxy("203.0.113.51", protocol=ProxyProtocol.HTTP, port=8080)
        make_proxy("203.0.113.52", latency_p95_ms=900)
        make_proxy("203.0.113.53", success_rate=0.5, probe_count=50)
        wanted = make_proxy("203.0.113.54")

        chain = self.chain(
            self.paid_user,
            {
                "protocol": ProxyProtocol.SOCKS5,
                "country": "NL",
                "max_latency_ms": 200,
                "min_success_rate": 0.95,
            },
        )
        self.assertEqual(resolve_chain(chain).proxy_ids, [wanted.pk])

    def test_country_matching_is_case_insensitive(self):
        proxy = make_proxy("203.0.113.60", country="nl")
        chain = self.chain(self.paid_user, {"country": "NL"})
        self.assertEqual(resolve_chain(chain).proxy_ids, [proxy.pk])

    def test_an_unprobed_proxy_cannot_satisfy_a_latency_guarantee(self):
        """A null p95 is not 'fast', it is 'unmeasured'. The hop asked for
        a guarantee and there is nothing to make it with."""
        make_proxy(
            "203.0.113.70",
            latency_p95_ms=None,
            latency_p50_ms=None,
            success_rate=None,
            probe_count=0,
            quality_score=50.0,
        )
        chain = self.chain(self.paid_user, {"max_latency_ms": 500})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        # Was `hop_unsatisfied` (P15). Still excluded -- the semantics are
        # unchanged -- but the failure now names *why* it was excluded,
        # because "measured and too slow" and "never measured" have
        # opposite remedies. See UnprobedDiagnosisTests.
        self.assertEqual(caught.exception.code, "hop_unsatisfied_unprobed")

    def test_an_unprobed_proxy_is_still_selectable_without_a_guarantee(self):
        """Otherwise a fresh CSV import resolves to nothing at all."""
        fresh = make_proxy(
            "203.0.113.71",
            latency_p95_ms=None,
            success_rate=None,
            probe_count=0,
            quality_score=50.0,
            last_checked=None,
        )
        chain = self.chain(self.paid_user, {"country": "NL"})
        self.assertEqual(resolve_chain(chain).proxy_ids, [fresh.pk])


# ---------------------------------------------------------------------------
# No proxy twice in one chain
# ---------------------------------------------------------------------------


class NoReuseWithinAChainTests(ResolutionTestCase):
    def test_two_constrained_hops_resolve_to_different_proxies(self):
        """A chain that resolves to the same proxy twice is not a chain:
        that operator sees both ends."""
        first = make_proxy("203.0.113.1", quality_score=95.0)
        second = make_proxy("203.0.113.2", quality_score=90.0)
        chain = self.chain(self.paid_user, {"country": "NL"}, {"country": "NL"})
        resolution = resolve_chain(chain)
        self.assertEqual(resolution.proxy_ids, [first.pk, second.pk])
        self.assertEqual(len(set(resolution.proxy_ids)), 2)

    def test_three_hops_from_three_candidates_uses_each_once(self):
        for index in range(3):
            make_proxy(f"203.0.113.{index}", quality_score=90.0 - index)
        chain = self.chain(self.paid_user, {}, {}, {})
        ids = resolve_chain(chain).proxy_ids
        self.assertEqual(len(set(ids)), 3)

    def test_a_constrained_hop_cannot_take_a_later_fixed_hops_proxy(self):
        """Fixed hops are reserved before anything is chosen. Without that,
        the greedy pass takes the best proxy for hop 0 and hop 1 -- which
        is pinned to it -- then fails."""
        pinned = make_proxy("203.0.113.10", quality_score=99.0)
        other = make_proxy("203.0.113.11", quality_score=80.0)
        chain = self.chain(self.paid_user, {}, {"proxy": pinned})
        resolution = resolve_chain(chain)
        self.assertEqual(resolution.proxy_ids, [other.pk, pinned.pk])

    def test_a_chain_needing_more_proxies_than_exist_fails_cleanly(self):
        make_proxy("203.0.113.20")
        chain = self.chain(self.paid_user, {}, {})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")
        self.assertEqual(caught.exception.position, 1)
        self.assertEqual(
            caught.exception.detail["already_used_proxy_ids"],
            [Proxy.objects.get(host="203.0.113.20").pk],
        )

    def test_two_fixed_hops_on_the_same_proxy_are_refused(self):
        """The database constraint prevents this on a saved chain, so the
        case is exercised on unsaved hops -- the preview path."""
        proxy = make_proxy("203.0.113.30")
        chain = ProxyChain.objects.create(user=self.paid_user, name="dup")
        hops = [
            ChainHop(chain=chain, position=0, proxy=proxy),
            ChainHop(chain=chain, position=1, proxy=proxy),
        ]
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_hops(hops, user=self.paid_user)
        self.assertEqual(caught.exception.code, "hop_duplicate_proxy")


# ---------------------------------------------------------------------------
# Hardest slot first
# ---------------------------------------------------------------------------


class AssignmentOrderTests(ResolutionTestCase):
    def test_a_narrow_slot_is_not_starved_by_a_wide_one(self):
        """Position order would fail this chain. There are two proxies; one
        is the only SOCKS5. Taking the best for the unconstrained hop 0
        leaves hop 1 -- which requires SOCKS5 -- with nothing.
        """
        only_socks = make_proxy(
            "203.0.113.1", protocol=ProxyProtocol.SOCKS5, quality_score=95.0
        )
        http = make_proxy(
            "203.0.113.2",
            protocol=ProxyProtocol.HTTP,
            port=8080,
            quality_score=60.0,
        )
        chain = self.chain(
            self.paid_user, {}, {"protocol": ProxyProtocol.SOCKS5}
        )
        resolution = resolve_chain(chain)
        self.assertEqual(resolution.proxy_ids, [http.pk, only_socks.pk])

    def test_results_come_back_in_position_order_not_assignment_order(self):
        make_proxy("203.0.113.10", protocol=ProxyProtocol.SOCKS5)
        make_proxy("203.0.113.11", protocol=ProxyProtocol.HTTP, port=8080)
        chain = self.chain(
            self.paid_user, {}, {"protocol": ProxyProtocol.SOCKS5}
        )
        resolution = resolve_chain(chain)
        self.assertEqual([hop.position for hop in resolution.hops], [0, 1])


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------


class EntitlementTests(ResolutionTestCase):
    def test_a_free_user_never_resolves_to_a_premium_proxy(self):
        make_proxy("203.0.113.1", tier=ProxyTier.PREMIUM, quality_score=99.0)
        free = make_proxy("203.0.113.2", tier=ProxyTier.FREE, quality_score=10.0)
        chain = self.chain(self.free_user, {})
        self.assertEqual(resolve_chain(chain).proxy_ids, [free.pk])

    def test_a_hop_asking_for_a_tier_the_plan_lacks_resolves_to_nothing(self):
        """The constraint can narrow the entitlement scope and never widen
        it, so this fails rather than quietly granting premium."""
        make_proxy("203.0.113.10", tier=ProxyTier.PREMIUM)
        make_proxy("203.0.113.11", tier=ProxyTier.FREE)
        chain = self.chain(self.free_user, {"tier": ProxyTier.PREMIUM})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")

    def test_a_fixed_hop_on_an_unentitled_proxy_is_refused(self):
        premium = make_proxy("203.0.113.20", tier=ProxyTier.PREMIUM)
        chain = self.chain(self.free_user, {"proxy": premium})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_proxy_not_entitled")
        # Says the plan does not cover it; does not say which tier is
        # missing.
        self.assertNotIn("premium", str(caught.exception.detail).lower())

    def test_an_expired_subscription_stops_a_saved_chain_resolving(self):
        """Reachable with no edit by the user at all: the chain was legal
        when it was saved and the plan shrank underneath it."""
        for index in range(3):
            make_proxy(f"203.0.113.3{index}", tier=ProxyTier.FREE)
        chain = self.chain(self.paid_user, {}, {}, {})
        self.assertEqual(len(resolve_chain(chain).hops), 3)

        Subscription.objects.filter(user=self.paid_user).update(status="expired")
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "chain_length_exceeded")
        self.assertEqual(caught.exception.detail["max_chain_length"], 1)

    def test_the_shared_entitlement_resolver_is_what_is_used(self):
        """Not a reimplementation: the same `allowed_proxy_tiers` tuple."""
        make_proxy("203.0.113.40", tier=ProxyTier.FREE)
        entitlement = resolve_entitlement(self.free_user)
        chain = self.chain(self.free_user, {})
        resolution = resolve_chain(chain)
        self.assertEqual(
            resolution.entitlement.allowed_proxy_tiers,
            entitlement.allowed_proxy_tiers,
        )
        self.assertIn(
            resolution.hops[0].proxy.tier, entitlement.allowed_proxy_tiers
        )

    def test_an_explicit_entitlement_can_be_passed_in(self):
        make_proxy("203.0.113.50", tier=ProxyTier.PREMIUM)
        chain = self.chain(self.free_user, {})
        resolution = resolve_hops(
            list(chain.hops.all()),
            entitlement=resolve_entitlement(self.paid_user),
        )
        self.assertEqual(len(resolution.hops), 1)


# ---------------------------------------------------------------------------
# Failing cleanly
# ---------------------------------------------------------------------------


class FailureReportingTests(ResolutionTestCase):
    def test_an_unsatisfiable_slot_names_the_position_and_the_constraints(self):
        make_proxy("203.0.113.1", country="DE")
        chain = self.chain(
            self.paid_user, {}, {"country": "FR", "min_success_rate": 0.99}
        )
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        exc = caught.exception
        self.assertEqual(exc.code, "hop_unsatisfied")
        self.assertEqual(exc.position, 1)
        self.assertEqual(exc.detail["constraints"]["country"], "FR")
        self.assertEqual(exc.detail["constraints"]["min_success_rate"], 0.99)

    def test_the_diagnostic_shows_which_clause_emptied_the_set(self):
        """'12 in NL, 9 enabled, 4 alive, 0 above 0.95' is the difference
        between fixing a chain and guessing at it."""
        for index in range(3):
            make_proxy(f"203.0.113.1{index}", success_rate=0.5, probe_count=50)
        chain = self.chain(self.paid_user, {"min_success_rate": 0.99})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)

        stages = {row["stage"]: row["remaining"] for row in caught.exception.detail["candidates"]}
        self.assertEqual(stages["all_proxies"], 3)
        self.assertEqual(stages["alive"], 3)
        self.assertEqual(stages["min_success_rate"], 0)

    def test_a_fixed_hop_on_a_dead_proxy_says_so_and_suggests_the_fix(self):
        dead = make_proxy("203.0.113.20", is_alive=False, consecutive_failures=4)
        chain = self.chain(self.paid_user, {"proxy": dead})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_proxy_dead")
        self.assertEqual(caught.exception.detail["consecutive_failures"], 4)
        self.assertIn("constrained hop", caught.exception.detail["hint"])

    def test_a_fixed_hop_on_a_disabled_proxy_says_so(self):
        disabled = make_proxy("203.0.113.30", is_enabled=False)
        chain = self.chain(self.paid_user, {"proxy": disabled})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_proxy_disabled")

    def test_an_empty_chain_is_refused(self):
        chain = ProxyChain.objects.create(user=self.paid_user, name="empty")
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "chain_empty")

    def test_as_detail_carries_the_code_and_position(self):
        chain = self.chain(self.paid_user, {"country": "ZZ"})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        detail = caught.exception.as_detail()
        self.assertEqual(detail["code"], "hop_unsatisfied")
        self.assertEqual(detail["position"], 0)
        self.assertIn("message", detail)


# ---------------------------------------------------------------------------
# Fixed hops keep working exactly as before
# ---------------------------------------------------------------------------


class FixedHopCompatibilityTests(ResolutionTestCase):
    def test_an_all_fixed_chain_resolves_to_itself(self):
        proxies = [make_proxy(f"203.0.113.{index}") for index in range(3)]
        chain = self.chain(
            self.paid_user, *[{"proxy": proxy} for proxy in proxies]
        )
        resolution = resolve_chain(chain)
        self.assertEqual(resolution.proxy_ids, [proxy.pk for proxy in proxies])
        self.assertTrue(all(hop.fixed for hop in resolution.hops))

    def test_a_fixed_hop_is_never_reported_as_failover_capable(self):
        proxy = make_proxy("203.0.113.10")
        make_proxy("203.0.113.11")
        chain = self.chain(self.paid_user, {"proxy": proxy})
        self.assertFalse(resolve_chain(chain).hops[0].is_failover_capable)

    def test_a_sole_candidate_is_not_failover_capable_either(self):
        make_proxy("203.0.113.20")
        chain = self.chain(self.paid_user, {"country": "NL"})
        hop = resolve_chain(chain).hops[0]
        self.assertEqual(hop.candidate_count, 1)
        self.assertFalse(hop.is_failover_capable)

    def test_mixed_chains_report_per_hop(self):
        pinned = make_proxy("203.0.113.30")
        make_proxy("203.0.113.31")
        make_proxy("203.0.113.32")
        chain = self.chain(self.paid_user, {"proxy": pinned}, {})
        resolution = resolve_chain(chain)
        self.assertTrue(resolution.hops[0].fixed)
        self.assertFalse(resolution.hops[1].fixed)
        self.assertEqual(resolution.hops[1].candidate_count, 2)


class CandidateQuerysetTests(ResolutionTestCase):
    def test_exclusions_are_honoured(self):
        first = make_proxy("203.0.113.1")
        second = make_proxy("203.0.113.2", quality_score=80.0)
        hop = ChainHop(position=0)
        entitlement = resolve_entitlement(self.paid_user)
        self.assertEqual(
            list(candidate_queryset(hop, entitlement)), [first, second]
        )
        self.assertEqual(
            list(candidate_queryset(hop, entitlement, [first.pk])), [second]
        )

    def test_ordering_is_best_first(self):
        make_proxy("203.0.113.10", quality_score=10.0)
        best = make_proxy("203.0.113.11", quality_score=99.0)
        hop = ChainHop(position=0)
        entitlement = resolve_entitlement(self.paid_user)
        self.assertEqual(candidate_queryset(hop, entitlement).first(), best)


# ---------------------------------------------------------------------------
# The database constraints behind the two hop shapes
# ---------------------------------------------------------------------------


class ChainHopConstraintTests(ResolutionTestCase):
    """The rules are enforced in the schema, not only in the serializer.

    The serializer is the friendly error; these are what stop a bad row
    whatever writes it -- a management command, a migration, the Django
    admin, or a future endpoint nobody has written yet.
    """

    def setUp(self):
        super().setUp()
        self.proxy = make_proxy("203.0.113.1")
        self.saved = ProxyChain.objects.create(user=self.paid_user, name="c")

    def test_a_hop_cannot_be_both_pinned_and_constrained(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChainHop.objects.create(
                    chain=self.saved, position=0, proxy=self.proxy, country="NL"
                )

    def test_several_constrained_hops_in_one_chain_do_not_collide(self):
        """`uniq_proxy_per_chain` is conditioned on a non-null proxy, so
        the rule does not depend on how a backend treats NULLs in a unique
        index. Several constrained hops per chain is the normal case."""
        for position in range(3):
            ChainHop.objects.create(
                chain=self.saved, position=position, country="NL"
            )
        self.assertEqual(self.saved.hops.count(), 3)

    def test_the_same_proxy_twice_in_one_chain_is_still_refused(self):
        from django.db import IntegrityError, transaction

        ChainHop.objects.create(chain=self.saved, position=0, proxy=self.proxy)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChainHop.objects.create(
                    chain=self.saved, position=1, proxy=self.proxy
                )

    def test_describe_constraints_reads_as_a_sentence(self):
        hop = ChainHop(
            position=0,
            protocol=ProxyProtocol.SOCKS5,
            country="NL",
            max_latency_ms=200,
            min_success_rate=0.95,
        )
        self.assertEqual(
            hop.describe_constraints(),
            "socks5 country=NL p95<=200ms success>=0.95",
        )

    def test_a_blank_constrained_hop_describes_itself_honestly(self):
        self.assertEqual(
            ChainHop(position=0).describe_constraints(), "any healthy proxy"
        )
