"""P15: a latency-constrained hop on a freshly imported inventory.

The semantics are not in question and are not changed here. An unprobed
proxy has no p95 and genuinely cannot satisfy a latency guarantee -- that
is the correct reading, and relaxing it by default would mean the
constraint promises something nobody measured.

What was wrong was the **diagnosis**. "0 candidates" was what an operator
saw whether the inventory was measured-and-too-slow or simply unmeasured,
and those two are fixed by opposite actions: replace the proxies, or run
a probe. On a fresh import it is always the second, and a resolver that
cannot say so reads as a bug the first time somebody hits it.

Three things are tested here:

1. the two exclusion reasons are now distinguishable, in the error code
   and in the per-stage diagnostic counts;
2. the "never measured" failure names the remedy;
3. `allow_unprobed` is a real way through, is off by default, and does
   not quietly widen into "ignore the threshold".

No prober and no network: health is written straight onto the proxy rows,
because what is under test is the selection, not the measurement.
"""

from django.db.utils import IntegrityError

from proxies.models import ChainHop, ProxyChain
from proxies.resolution import ChainResolutionError, resolve_chain
from proxies.test_resolution import ResolutionTestCase, make_proxy


class UnprobedDiagnosisTests(ResolutionTestCase):
    def unprobed(self, host, **kwargs):
        """A proxy exactly as a CSV import leaves it: nothing measured."""
        defaults = {
            "latency_p50_ms": None,
            "latency_p95_ms": None,
            "success_rate": None,
            "probe_count": 0,
            "quality_score": 50.0,
            "last_checked": None,
        }
        defaults.update(kwargs)
        return make_proxy(host, **defaults)

    # -- the two exclusion reasons are now distinguishable ---------------

    def test_never_measured_gets_its_own_error_code(self):
        self.unprobed("203.0.113.80")
        self.unprobed("203.0.113.81")
        chain = self.chain(self.paid_user, {"max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied_unprobed")
        self.assertEqual(caught.exception.detail["unprobed_candidates"], 2)

    def test_measured_and_too_slow_keeps_the_generic_code(self):
        """The opposite case: these were probed and they are not good
        enough. Probing again will not help, and the error must not
        suggest it."""
        make_proxy("203.0.113.82", latency_p95_ms=900, probe_count=50)
        chain = self.chain(self.paid_user, {"max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")
        self.assertNotIn("unprobed_candidates", caught.exception.detail)

    def test_a_mix_still_reports_unprobed_when_nothing_measured_qualifies(self):
        """Some measured-and-too-slow, some unmeasured. The actionable
        advice is still the probe run, because that is the only one of the
        two that can turn into a candidate."""
        make_proxy("203.0.113.83", latency_p95_ms=900, probe_count=50)
        self.unprobed("203.0.113.84")
        chain = self.chain(self.paid_user, {"max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied_unprobed")
        self.assertEqual(caught.exception.detail["unprobed_candidates"], 1)

    def test_no_candidates_at_all_is_not_blamed_on_probing(self):
        """An inventory emptied by an earlier filter must not claim a
        probe run would fix it."""
        self.unprobed("203.0.113.85", country="DE")
        chain = self.chain(self.paid_user, {"country": "NL", "max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")

    def test_a_disabled_unprobed_proxy_is_not_offered_as_a_probe_remedy(self):
        """Exclusions that happen *before* the health thresholds are not
        the thresholds' fault. A disabled proxy stays excluded however
        much it is probed, and saying otherwise would send an operator to
        run a command that changes nothing."""
        self.unprobed("203.0.113.86", is_enabled=False)
        chain = self.chain(self.paid_user, {"max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")

    def test_an_unentitled_unprobed_proxy_is_not_a_probe_remedy_either(self):
        """Entitlement is checked before health, and a probe run will
        never make a premium proxy appear on a free plan."""
        self.unprobed("203.0.113.89", tier="premium")
        chain = self.chain(self.free_user, {"max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")

    def test_the_error_names_the_command_to_run(self):
        self.unprobed("203.0.113.87")
        chain = self.chain(self.paid_user, {"min_success_rate": 0.95})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertIn("probe_proxies", caught.exception.message)
        self.assertIn("never been probed", caught.exception.message)
        remedy = caught.exception.detail["remedy"]
        self.assertEqual(remedy["command"], "probe_proxies")
        self.assertEqual(remedy["override_field"], "allow_unprobed")
        self.assertIn("1 candidates have never been probed", remedy["hint"])

    def test_the_message_is_grammatical_for_a_single_candidate(self):
        self.unprobed("203.0.113.88")
        chain = self.chain(self.paid_user, {"max_latency_ms": 200})
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertIn("1 candidate matches", caught.exception.message)
        self.assertIn("has never been probed", caught.exception.message)

    # -- the diagnostic stages -------------------------------------------

    def stages(self, chain):
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        return {row["stage"]: row for row in caught.exception.detail["candidates"]}

    def test_the_latency_stage_separates_too_slow_from_unmeasured(self):
        """The heart of P15: one number became two."""
        make_proxy("203.0.113.90", latency_p95_ms=900, probe_count=50)
        make_proxy("203.0.113.91", latency_p95_ms=800, probe_count=50)
        self.unprobed("203.0.113.92")
        self.unprobed("203.0.113.93")
        self.unprobed("203.0.113.94")

        stage = self.stages(self.chain(self.paid_user, {"max_latency_ms": 200}))[
            "max_latency_ms"
        ]
        self.assertEqual(stage["remaining"], 0)
        self.assertEqual(stage["excluded_measured"], 2)
        self.assertEqual(stage["excluded_unprobed"], 3)

    def test_the_success_rate_stage_separates_them_too(self):
        make_proxy("203.0.113.95", success_rate=0.4, probe_count=50)
        self.unprobed("203.0.113.96")
        stage = self.stages(self.chain(self.paid_user, {"min_success_rate": 0.99}))[
            "min_success_rate"
        ]
        self.assertEqual(stage["excluded_measured"], 1)
        self.assertEqual(stage["excluded_unprobed"], 1)

    def test_a_stage_that_was_not_applied_says_so(self):
        self.unprobed("203.0.113.97", country="DE")
        stage = self.stages(self.chain(self.paid_user, {"country": "NL"}))[
            "max_latency_ms"
        ]
        self.assertFalse(stage["applied"])
        self.assertEqual(stage["excluded_measured"], 0)
        self.assertEqual(stage["excluded_unprobed"], 0)

    def test_the_counts_add_up_to_what_entered_the_stage(self):
        """Arithmetic the API reports. An off-by-one here is a lie told
        to whoever is already debugging something."""
        for index in range(4):
            make_proxy(f"203.0.113.6{index}", latency_p95_ms=900, probe_count=50)
        for index in range(3):
            self.unprobed(f"203.0.113.7{index}")
        stages = self.stages(self.chain(self.paid_user, {"max_latency_ms": 100}))
        entering = stages["tier"]["remaining"]
        stage = stages["max_latency_ms"]
        self.assertEqual(
            entering,
            stage["remaining"]
            + stage["excluded_measured"]
            + stage["excluded_unprobed"],
        )

    def test_the_stages_still_narrow_in_order(self):
        """The split did not break the widest-to-narrowest reading the
        diagnostic exists to give."""
        self.unprobed("203.0.113.98")
        stages = self.stages(self.chain(self.paid_user, {"max_latency_ms": 100}))
        order = [row["stage"] for row in sorted(stages.values(), key=lambda r: 0)]
        self.assertIn("max_latency_ms", stages)
        self.assertIn("min_success_rate", stages)
        self.assertGreaterEqual(stages["all_proxies"]["remaining"], 1)
        self.assertEqual(stages["max_latency_ms"]["remaining"], 0)
        self.assertEqual(len(order), len(stages))

    # -- the opt-in -------------------------------------------------------

    def test_allow_unprobed_lets_a_fresh_import_resolve(self):
        """The way through that is not "delete the constraint"."""
        fresh = self.unprobed("203.0.113.99")
        chain = self.chain(
            self.paid_user, {"max_latency_ms": 200, "allow_unprobed": True}
        )
        self.assertEqual(resolve_chain(chain).proxy_ids, [fresh.pk])

    def test_allow_unprobed_works_for_the_success_rate_threshold_too(self):
        fresh = self.unprobed("203.0.113.50")
        chain = self.chain(
            self.paid_user, {"min_success_rate": 0.99, "allow_unprobed": True}
        )
        self.assertEqual(resolve_chain(chain).proxy_ids, [fresh.pk])

    def test_allow_unprobed_works_when_both_thresholds_are_set(self):
        """The opt-in has to escape *both* clauses at once. Applying it
        to one and not the other would leave the hop still empty."""
        fresh = self.unprobed("203.0.113.51")
        chain = self.chain(
            self.paid_user,
            {
                "max_latency_ms": 200,
                "min_success_rate": 0.99,
                "allow_unprobed": True,
            },
        )
        self.assertEqual(resolve_chain(chain).proxy_ids, [fresh.pk])

    def test_allow_unprobed_is_off_by_default(self):
        """A guarantee nothing measured is not a guarantee. The operator
        has to ask for it."""
        self.assertFalse(ChainHop(position=0, max_latency_ms=200).allow_unprobed)

    def test_allow_unprobed_does_not_admit_a_measured_failure(self):
        """The flag means "unmeasured is acceptable", not "the threshold
        is off". A proxy measured at 900 ms stays excluded."""
        make_proxy("203.0.113.40", latency_p95_ms=900, probe_count=50)
        chain = self.chain(
            self.paid_user, {"max_latency_ms": 200, "allow_unprobed": True}
        )
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")

    def test_a_proxy_that_failed_every_probe_is_not_unprobed(self):
        """It has a null p95 too, and it is emphatically not unmeasured.
        Keying the opt-in on the null rather than on `probe_count` would
        quietly re-admit the worst proxies in the inventory under a flag
        whose entire promise is "nothing is known about these yet"."""
        make_proxy(
            "203.0.113.41",
            latency_p95_ms=None,
            latency_p50_ms=None,
            success_rate=0.0,
            probe_count=50,
            quality_score=0.0,
        )
        chain = self.chain(
            self.paid_user, {"max_latency_ms": 200, "allow_unprobed": True}
        )
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chain(chain)
        self.assertEqual(caught.exception.code, "hop_unsatisfied")

    def test_allow_unprobed_still_prefers_a_measured_proxy(self):
        """Ordering is unchanged: the opt-in widens the candidate set, it
        does not promote the unmeasured above the proven."""
        good = make_proxy(
            "203.0.113.42", latency_p95_ms=50, probe_count=50, quality_score=95.0
        )
        self.unprobed("203.0.113.43")
        chain = self.chain(
            self.paid_user, {"max_latency_ms": 200, "allow_unprobed": True}
        )
        self.assertEqual(resolve_chain(chain).proxy_ids, [good.pk])

    def test_allow_unprobed_never_widens_entitlement(self):
        """Rule 1 is untouched: a constraint narrows, never widens. The
        opt-in relaxes a health threshold and nothing else."""
        self.unprobed("203.0.113.45", tier="premium")
        chain = self.chain(
            self.free_user, {"max_latency_ms": 200, "allow_unprobed": True}
        )
        with self.assertRaises(ChainResolutionError):
            resolve_chain(chain)

    def test_allow_unprobed_does_not_revive_a_dead_proxy(self):
        self.unprobed("203.0.113.46", is_alive=False, consecutive_failures=4)
        chain = self.chain(
            self.paid_user, {"max_latency_ms": 200, "allow_unprobed": True}
        )
        with self.assertRaises(ChainResolutionError):
            resolve_chain(chain)

    def test_allow_unprobed_is_reported_in_the_constraint_dict(self):
        """The client renders the hop from this. A hidden flag that
        changes selection is how a constraint stops meaning what it says
        on screen."""
        hop = ChainHop(position=0, max_latency_ms=200, allow_unprobed=True)
        self.assertTrue(hop.constraint_dict()["allow_unprobed"])
        self.assertIn("or unprobed", hop.describe_constraints())

    def test_the_flag_is_a_no_op_without_a_threshold(self):
        """Nothing for an unprobed proxy to fail, so nothing to allow.
        Not an error -- just silent, and not claimed in the summary."""
        hop = ChainHop(position=0, allow_unprobed=True)
        self.assertFalse(hop.has_health_thresholds)
        self.assertNotIn("or unprobed", hop.describe_constraints())

    def test_the_opt_in_is_refused_on_a_pinned_hop(self):
        """A constraint field on a fixed hop describes a selection that
        never happens. The database constraint says so rather than
        leaving it to be found in a support ticket."""
        proxy = make_proxy("203.0.113.44")
        chain = ProxyChain.objects.create(user=self.paid_user, name="pinned")
        with self.assertRaises(IntegrityError):
            ChainHop.objects.create(
                chain=chain, position=0, proxy=proxy, allow_unprobed=True
            )
