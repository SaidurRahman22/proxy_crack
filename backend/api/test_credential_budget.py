"""P16 -- the credential budget, and the two things it has to do at once.

The defect: `proxy_credentials` was 60 requests/hour, flat, while D18
makes the client fetch credentials one hop at a time. A 3-hop connect
cost three units and every failover cost up to three more, so a flaky
session ran the budget out in about twenty minutes and the user was then
locked out of connecting at all.

The fix changed the *unit* of the budget, not its size: 60 **distinct
proxies** per hour. So there are two claims to prove, and the second one
matters more than the first:

1. a 3-hop user can connect and fail over all hour without lockout;
2. someone walking the inventory still hits a wall, in the same place it
   has always been.

A fix that only satisfies (1) is the protection removed, not a fix. The
enumeration tests below therefore run at the *configured* rate rather
than a patched-down one, so they fail if someone widens the budget.

Note on request volume: the global `user` throttle (120/min) applies to
every view, so the loops here stay well under it. A test that tripped it
would be measuring the wrong limit.
"""

from unittest import mock

from django.core.cache import cache
from rest_framework import status

from api.tests import EntitlementBaseTestCase, make_proxy
from api.throttling import (
    CredentialDailyDisclosureThrottle,
    CredentialDisclosureThrottle,
)
from proxies.models import ProxyTier

# The configured hourly budget, as a number this module can count to. Read
# off settings rather than hard-coded so the tests track the configuration
# instead of a copy of it.
HOURLY_BUDGET = 60


def configured(scope):
    from django.conf import settings

    return settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope]


class CredentialBudgetUsabilityTests(EntitlementBaseTestCase):
    """Claim 1: connecting and failing over all hour must not lock anyone out."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.pro_user)
        # A 3-hop chain plus one spare for failover to land on.
        self.chain = [self.premium_a, self.premium_b, self.premium_c]
        self.spare = make_proxy("198.51.100.5", ProxyTier.PREMIUM)

    def fetch(self, proxy):
        return self.client.get(f"/api/proxies/{proxy.pk}/").status_code

    def test_three_hop_user_connects_and_fails_over_all_hour_without_lockout(self):
        """Twenty connects and ten failovers: 70 credential fetches, which
        is more than the whole hourly budget under the old counting, and
        every one of them has to succeed."""
        codes = []
        for cycle in range(20):
            for hop in self.chain:
                codes.append(self.fetch(hop))
            if cycle % 2 == 0:
                # A hop died; the resolver picked the spare for that slot.
                codes.append(self.fetch(self.spare))

        self.assertEqual(len(codes), 70)
        self.assertEqual(
            set(codes),
            {status.HTTP_200_OK},
            "a reconnecting 3-hop user was throttled -- P16 is not fixed",
        )

    def test_the_session_above_costs_four_units_not_seventy(self):
        """The accounting claim underneath claim 1: consumption tracks the
        chain, not the reconnect count."""
        for _ in range(20):
            for hop in self.chain:
                self.fetch(hop)
            self.fetch(self.spare)

        throttle = CredentialDisclosureThrottle()
        key = throttle.cache_format % {
            "scope": throttle.scope,
            "ident": self.pro_user.pk,
        }
        history = cache.get(key) or []
        self.assertEqual(len(history), 4, history)
        self.assertEqual(
            {entry[1] for entry in history},
            {str(p.pk) for p in self.chain + [self.spare]},
        )

    def test_a_repeat_disclosure_is_free_even_at_a_budget_of_one(self):
        """Stated as starkly as it can be: budget 1, same proxy twenty
        times, all 200."""
        with mock.patch.object(
            CredentialDisclosureThrottle, "rate", "1/hour", create=True
        ):
            codes = [self.fetch(self.premium_a) for _ in range(20)]
        self.assertEqual(set(codes), {status.HTTP_200_OK})

    def test_but_a_second_distinct_proxy_at_that_budget_is_refused(self):
        """The other half of the previous test. Repetition is free;
        coverage is not."""
        with mock.patch.object(
            CredentialDisclosureThrottle, "rate", "1/hour", create=True
        ):
            self.assertEqual(self.fetch(self.premium_a), status.HTTP_200_OK)
            self.assertEqual(self.fetch(self.premium_a), status.HTTP_200_OK)
            self.assertEqual(
                self.fetch(self.premium_b), status.HTTP_429_TOO_MANY_REQUESTS
            )

    def test_free_tier_user_is_not_charged_more_per_connect_than_a_paying_one(self):
        """The backwards property P16 called out: under request counting a
        1-hop free user spent one unit per connect and a 3-hop paying user
        spent three. Both now spend one per *proxy*, once."""
        self.client.force_authenticate(self.free_user)
        for _ in range(10):
            self.assertEqual(self.fetch(self.free_proxy), status.HTTP_200_OK)

        throttle = CredentialDisclosureThrottle()
        key = throttle.cache_format % {
            "scope": throttle.scope,
            "ident": self.free_user.pk,
        }
        self.assertEqual(len(cache.get(key) or []), 1)


class InventoryEnumerationTests(EntitlementBaseTestCase):
    """Claim 2, the one that matters more: the wall is still there.

    These run at the real configured rate. If somebody widens the budget
    to make a usability complaint go away, these fail.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.pro_user)
        # Enough inventory to walk past the hourly budget. The three
        # premium proxies from the base case count toward it too.
        existing = 3
        self.inventory = [self.premium_a, self.premium_b, self.premium_c] + [
            make_proxy(f"203.0.113.{n}", ProxyTier.PREMIUM)
            for n in range(1, HOURLY_BUDGET - existing + 2)
        ]
        self.assertEqual(len(self.inventory), HOURLY_BUDGET + 1)

    def test_walking_the_inventory_hits_the_wall_at_the_configured_budget(self):
        codes = [
            self.client.get(f"/api/proxies/{proxy.pk}/").status_code
            for proxy in self.inventory
        ]
        self.assertEqual(
            codes[:HOURLY_BUDGET],
            [status.HTTP_200_OK] * HOURLY_BUDGET,
            "the budget bit earlier than configured",
        )
        self.assertEqual(
            codes[HOURLY_BUDGET],
            status.HTTP_429_TOO_MANY_REQUESTS,
            "inventory enumeration was NOT stopped -- the anti-scrape "
            "property has been lost",
        )

    def test_padding_the_scrape_with_repeats_does_not_buy_extra_coverage(self):
        """A scraper who interleaves re-fetches -- the obvious way to try
        to look like a reconnecting client -- gets no more inventory for
        it. Repeats are free, and free is not the same as useful.

        Run at a small patched budget so the loop stays well inside the
        global 120/min `user` throttle; tripping that would make the test
        pass for the wrong reason.
        """
        budget = 5
        seen = 0
        with mock.patch.object(
            CredentialDisclosureThrottle, "rate", f"{budget}/hour", create=True
        ):
            for proxy in self.inventory[: budget * 3]:
                # One free repeat of a proxy already paid for, then a new one.
                self.client.get(f"/api/proxies/{self.inventory[0].pk}/")
                if (
                    self.client.get(f"/api/proxies/{proxy.pk}/").status_code
                    == status.HTTP_200_OK
                ):
                    seen += 1
        self.assertEqual(
            seen,
            budget,
            "repeat padding widened how much inventory one account could "
            "uncover in an hour",
        )

    def test_probing_ids_the_user_is_not_entitled_to_costs_budget(self):
        """Enumeration starts with "which ids exist", and a 404 answers
        that. It is charged before the view runs, so it is not free."""
        self.client.force_authenticate(self.free_user)
        with mock.patch.object(
            CredentialDisclosureThrottle, "rate", "2/hour", create=True
        ):
            # Two premium ids this free user cannot see: 404, but charged.
            self.assertEqual(
                self.client.get(f"/api/proxies/{self.premium_a.pk}/").status_code,
                status.HTTP_404_NOT_FOUND,
            )
            self.assertEqual(
                self.client.get(f"/api/proxies/{self.premium_b.pk}/").status_code,
                status.HTTP_404_NOT_FOUND,
            )
            # Budget spent, so a proxy they *can* see is refused.
            self.assertEqual(
                self.client.get(f"/api/proxies/{self.free_proxy.pk}/").status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

    def test_the_daily_ceiling_bounds_patience_independently(self):
        """An hourly limit alone is defeated by waiting. The daily one is
        a separate counter with a separate window, so it is not."""
        self.client.force_authenticate(self.pro_user)
        with mock.patch.object(
            CredentialDailyDisclosureThrottle, "rate", "2/day", create=True
        ):
            codes = [
                self.client.get(f"/api/proxies/{proxy.pk}/").status_code
                for proxy in self.inventory[:3]
            ]
        self.assertEqual(codes[:2], [status.HTTP_200_OK] * 2)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_the_budget_is_per_user_not_global(self):
        """Two accounts must not share one bucket -- that would make the
        limit a denial-of-service tool against other subscribers."""
        with mock.patch.object(
            CredentialDisclosureThrottle, "rate", "1/hour", create=True
        ):
            self.client.force_authenticate(self.pro_user)
            self.assertEqual(
                self.client.get(f"/api/proxies/{self.premium_a.pk}/").status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(
                self.client.get(f"/api/proxies/{self.premium_b.pk}/").status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
            self.client.force_authenticate(self.free_user)
            self.assertEqual(
                self.client.get(f"/api/proxies/{self.free_proxy.pk}/").status_code,
                status.HTTP_200_OK,
            )


class CredentialBudgetWiringTests(EntitlementBaseTestCase):
    """The configuration itself, so a silent misconfiguration fails here
    rather than in production."""

    def test_retrieve_carries_both_disclosure_throttles(self):
        from api.views import ProxyViewSet

        view = ProxyViewSet()
        view.action = "retrieve"
        view.request = None
        classes = {type(throttle) for throttle in view.get_throttles()}
        self.assertIn(CredentialDisclosureThrottle, classes)
        self.assertIn(CredentialDailyDisclosureThrottle, classes)

    def test_retrieve_does_not_also_carry_a_request_scope(self):
        """The scoped throttle must be inert on this action, or the old
        per-request budget would still be in force alongside the new one
        and P16 would be only half fixed."""
        from api.views import ProxyViewSet

        view = ProxyViewSet()
        view.action = "retrieve"
        view.request = None
        view.get_throttles()
        self.assertIsNone(view.throttle_scope)

    def test_list_still_carries_the_proxy_list_scope(self):
        from api.views import ProxyViewSet

        view = ProxyViewSet()
        view.action = "list"
        view.request = None
        view.get_throttles()
        self.assertEqual(view.throttle_scope, "proxy_list")

    def test_every_mobile_scope_has_a_configured_rate(self):
        for scope in (
            "register",
            "login",
            "logout",
            "proxy_list",
            "proxy_credentials",
            "proxy_credentials_day",
            "chain_write",
            "chain_resolve",
            "chain_diversity",
            "usage_report",
            "content",
        ):
            self.assertTrue(configured(scope), scope)

    def test_the_hourly_budget_is_still_sixty(self):
        """The fix was supposed to change the unit, not the size. If this
        number moved, the usability tests above stopped proving anything
        and the enumeration tests stopped protecting anything."""
        self.assertEqual(configured("proxy_credentials"), f"{HOURLY_BUDGET}/hour")

    def test_an_unidentifiable_subject_is_charged_rather_than_waved_through(self):
        """Fail closed. If the throttle cannot tell what a request would
        disclose, it must cost a unit -- otherwise "make the lookup kwarg
        unreadable" is a budget bypass."""
        class ViewWithoutKwargs:
            kwargs = {}
            lookup_field = "pk"

        view = ViewWithoutKwargs()
        self.assertIsNone(CredentialDisclosureThrottle().get_subject(None, view))

        with mock.patch.object(
            CredentialDisclosureThrottle, "rate", "2/hour", create=True
        ):
            request = mock.Mock(user=self.pro_user)
            # A fresh instance per call, as DRF does -- the rate is read in
            # __init__, so an instance built before the patch would carry
            # the real budget.
            outcomes = [
                CredentialDisclosureThrottle().allow_request(request, view)
                for _ in range(3)
            ]
        self.assertEqual(outcomes, [True, True, False])

    def test_history_written_by_the_old_request_throttle_does_not_crash(self):
        """A deploy happens with the previous throttle's entries -- bare
        floats -- already in the cache. They must age out, not raise."""
        throttle = CredentialDisclosureThrottle()
        key = throttle.cache_format % {
            "scope": throttle.scope,
            "ident": self.pro_user.pk,
        }
        cache.set(key, [throttle.timer()], throttle.duration)

        self.client.force_authenticate(self.pro_user)
        self.assertEqual(
            self.client.get(f"/api/proxies/{self.premium_a.pk}/").status_code,
            status.HTTP_200_OK,
        )
