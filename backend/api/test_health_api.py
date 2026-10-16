"""Mobile API: health on the proxy list, constrained hops, and resolution.

Covers the three things the client can now do that it could not before:
see *how* healthy a proxy is rather than a boolean, save a hop as a
constraint, and ask the server to turn that chain into concrete proxies.

The credential boundary is re-asserted here: `/resolve/` must not become a
way to pull three sets of paid credentials in one unthrottled request.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Plan, Subscription
from proxies.models import (
    ChainHop,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
)

User = get_user_model()
PASSWORD = "Str0ngPassphrase!"

HEALTH_FIELDS = (
    "is_alive",
    "latency_ms",
    "last_checked",
    "success_rate",
    "probe_count",
    "consecutive_failures",
    "latency_p50_ms",
    "latency_p95_ms",
    "quality_score",
)
CREDENTIAL_KEYS = ("username", "password", "ss_method")


def make_proxy(host, **kwargs):
    defaults = {
        "protocol": ProxyProtocol.SOCKS5,
        "port": 1080,
        "tier": ProxyTier.PREMIUM,
        "country": "NL",
        "success_rate": 1.0,
        "probe_count": 50,
        "latency_ms": 90,
        "latency_p50_ms": 80,
        "latency_p95_ms": 100,
        "quality_score": 90.0,
        "last_checked": timezone.now(),
        "username": "paid-user",
        "password": "paid-pass",
    }
    defaults.update(kwargs)
    return Proxy.objects.create(host=host, **defaults)


class HealthApiTestCase(APITestCase):
    def setUp(self):
        cache.clear()
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
        self.free_user = User.objects.create_user("free@example.com", PASSWORD)
        self.paid_user = User.objects.create_user("paid@example.com", PASSWORD)
        Subscription.objects.create(
            user=self.paid_user,
            plan=self.pro_plan,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def as_paid(self):
        self.client.force_authenticate(self.paid_user)
        return self.client

    def as_free(self):
        self.client.force_authenticate(self.free_user)
        return self.client


# ---------------------------------------------------------------------------
# Health on the proxy list
# ---------------------------------------------------------------------------


class ProxyHealthFieldsTests(HealthApiTestCase):
    def setUp(self):
        super().setUp()
        self.proxy = make_proxy("203.0.113.1")

    def test_the_list_carries_the_full_health_block(self):
        row = self.as_paid().get("/api/proxies/").data["results"][0]
        for field in HEALTH_FIELDS:
            self.assertIn(field, row, f"{field} missing from the proxy list")
        self.assertEqual(row["success_rate"], 1.0)
        self.assertEqual(row["latency_p95_ms"], 100)
        self.assertEqual(row["quality_score"], 90.0)

    def test_health_is_not_a_credential_leak(self):
        row = self.as_paid().get("/api/proxies/").data["results"][0]
        for key in CREDENTIAL_KEYS:
            self.assertNotIn(key, row)

    def test_the_default_ordering_is_best_first_not_fastest_first(self):
        """The difference is the whole point of the scoring model."""
        make_proxy(
            "203.0.113.2",
            latency_ms=5,
            latency_p95_ms=5,
            success_rate=0.4,
            quality_score=25.0,
        )
        hosts = [
            row["host"] for row in self.as_paid().get("/api/proxies/").data["results"]
        ]
        self.assertEqual(hosts[0], "203.0.113.1")

    def test_filtering_by_success_rate(self):
        make_proxy("203.0.113.3", success_rate=0.5, quality_score=30.0)
        response = self.as_paid().get("/api/proxies/?min_success_rate=0.9")
        self.assertEqual(
            [row["host"] for row in response.data["results"]], ["203.0.113.1"]
        )

    def test_filtering_by_p95(self):
        make_proxy("203.0.113.4", latency_p95_ms=900)
        response = self.as_paid().get("/api/proxies/?max_p95_latency_ms=200")
        self.assertEqual(
            [row["host"] for row in response.data["results"]], ["203.0.113.1"]
        )

    def test_filtering_by_quality_score(self):
        make_proxy("203.0.113.5", quality_score=12.0)
        response = self.as_paid().get("/api/proxies/?min_quality_score=50")
        self.assertEqual(len(response.data["results"]), 1)

    def test_ordering_by_a_health_field_is_allowed(self):
        make_proxy("203.0.113.6", latency_p95_ms=10, quality_score=20.0)
        response = self.as_paid().get("/api/proxies/?ordering=latency_p95_ms")
        self.assertEqual(response.data["results"][0]["host"], "203.0.113.6")

    def test_health_filters_still_cannot_widen_entitlement(self):
        make_proxy("203.0.113.7", tier=ProxyTier.PREMIUM, quality_score=99.0)
        make_proxy("203.0.113.8", tier=ProxyTier.FREE, quality_score=99.0)
        response = self.as_free().get("/api/proxies/?min_quality_score=0")
        self.assertEqual(
            {row["tier"] for row in response.data["results"]}, {ProxyTier.FREE}
        )


# ---------------------------------------------------------------------------
# Writing constrained hops
# ---------------------------------------------------------------------------


class ConstrainedHopWriteTests(HealthApiTestCase):
    def test_proxy_ids_still_works_unchanged(self):
        """The Phase 1 write shape is load-bearing for the shipped client."""
        proxy = make_proxy("203.0.113.1")
        response = self.as_paid().post(
            "/api/chains/", {"name": "classic", "proxy_ids": [proxy.pk]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["hops"][0]["proxy"]["id"], proxy.pk)
        self.assertFalse(response.data["has_constrained_hops"])

    def test_a_constrained_hop_is_saved_with_a_null_proxy(self):
        response = self.as_paid().post(
            "/api/chains/",
            {
                "name": "flexible",
                "hop_specs": [
                    {
                        "protocol": ProxyProtocol.SOCKS5,
                        "country": "NL",
                        "max_latency_ms": 200,
                        "min_success_rate": 0.95,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        hop = response.data["hops"][0]
        self.assertIsNone(hop["proxy"])
        self.assertTrue(hop["is_constrained"])
        self.assertEqual(hop["country"], "NL")
        self.assertEqual(hop["max_latency_ms"], 200)
        self.assertTrue(response.data["has_constrained_hops"])

    def test_a_mixed_chain_is_allowed(self):
        proxy = make_proxy("203.0.113.10")
        response = self.as_paid().post(
            "/api/chains/",
            {
                "name": "mixed",
                "hop_specs": [{"proxy": proxy.pk}, {"country": "NL"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["hops"][0]["proxy"]["id"], proxy.pk)
        self.assertIsNone(response.data["hops"][1]["proxy"])

    def test_a_hop_cannot_be_both_pinned_and_constrained(self):
        proxy = make_proxy("203.0.113.20")
        response = self.as_paid().post(
            "/api/chains/",
            {
                "name": "confused",
                "hop_specs": [{"proxy": proxy.pk, "country": "NL"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_both_write_shapes_at_once_is_refused(self):
        proxy = make_proxy("203.0.113.30")
        response = self.as_paid().post(
            "/api/chains/",
            {"name": "x", "proxy_ids": [proxy.pk], "hop_specs": [{"country": "NL"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_neither_write_shape_is_refused_on_create(self):
        response = self.as_paid().post("/api/chains/", {"name": "x"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_chain_length_is_enforced_on_hop_specs_too(self):
        response = self.as_free().post(
            "/api/chains/",
            {"name": "long", "hop_specs": [{"country": "NL"}, {"country": "DE"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "chain_length_exceeded")

    def test_a_constrained_hop_cannot_ask_for_an_unentitled_tier(self):
        """Caught at write time so the user gets a clear error now, rather
        than an unsatisfiable slot at connect time."""
        response = self.as_free().post(
            "/api/chains/",
            {"name": "greedy", "hop_specs": [{"tier": ProxyTier.PREMIUM}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "proxy_not_entitled")

    def test_a_pinned_unentitled_proxy_is_still_refused(self):
        premium = make_proxy("203.0.113.40", tier=ProxyTier.PREMIUM)
        response = self.as_free().post(
            "/api/chains/",
            {"name": "greedy", "hop_specs": [{"proxy": premium.pk}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "proxy_not_entitled")

    def test_the_same_proxy_twice_is_refused(self):
        proxy = make_proxy("203.0.113.50")
        response = self.as_paid().post(
            "/api/chains/",
            {"name": "dup", "hop_specs": [{"proxy": proxy.pk}, {"proxy": proxy.pk}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_update_can_convert_a_fixed_chain_to_a_constrained_one(self):
        """The migration path a user takes after a pinned hop dies."""
        proxy = make_proxy("203.0.113.60")
        chain = self.as_paid().post(
            "/api/chains/", {"name": "x", "proxy_ids": [proxy.pk]}, format="json"
        ).data
        response = self.client.patch(
            f"/api/chains/{chain['id']}/",
            {"hop_specs": [{"country": "NL"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data["hops"][0]["is_constrained"])


# ---------------------------------------------------------------------------
# The resolve endpoint
# ---------------------------------------------------------------------------


class ChainResolveEndpointTests(HealthApiTestCase):
    def saved_chain(self, user, *hop_kwargs, name="route"):
        chain = ProxyChain.objects.create(user=user, name=name)
        for position, kwargs in enumerate(hop_kwargs):
            ChainHop.objects.create(chain=chain, position=position, **kwargs)
        return chain

    def test_a_constrained_chain_resolves_to_concrete_proxies(self):
        best = make_proxy("203.0.113.1", quality_score=95.0)
        make_proxy("203.0.113.2", quality_score=40.0)
        chain = self.saved_chain(self.paid_user, {"country": "NL"})

        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        hop = response.data["hops"][0]
        self.assertEqual(hop["proxy"]["id"], best.pk)
        self.assertEqual(hop["position"], 0)
        self.assertFalse(hop["fixed"])
        self.assertEqual(hop["candidate_count"], 2)
        self.assertTrue(hop["failover_capable"])
        self.assertEqual(response.data["chain"]["id"], chain.pk)

    def test_resolution_never_returns_credentials(self):
        """A resolve call must not become bulk credential disclosure."""
        make_proxy("203.0.113.10")
        chain = self.saved_chain(self.paid_user, {"country": "NL"})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        body = str(response.data)
        for key in CREDENTIAL_KEYS:
            self.assertNotIn(f"'{key}'", body)
        self.assertNotIn("paid-pass", body)

    def test_each_hop_points_at_the_throttled_credential_endpoint(self):
        proxy = make_proxy("203.0.113.11")
        chain = self.saved_chain(self.paid_user, {"country": "NL"})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertIn(
            reverse("api:proxy-detail", args=[proxy.pk]),
            response.data["hops"][0]["credentials_url"],
        )

    def test_a_three_hop_chain_resolves_without_reusing_a_proxy(self):
        for index in range(4):
            make_proxy(f"203.0.113.2{index}", quality_score=90.0 - index)
        chain = self.saved_chain(self.paid_user, {}, {}, {})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        ids = [hop["proxy"]["id"] for hop in response.data["hops"]]
        self.assertEqual(len(set(ids)), 3)

    def test_a_fixed_chain_resolves_to_itself(self):
        proxy = make_proxy("203.0.113.30")
        chain = self.saved_chain(self.paid_user, {"proxy": proxy})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.data["hops"][0]["proxy"]["id"], proxy.pk)
        self.assertTrue(response.data["hops"][0]["fixed"])
        self.assertFalse(response.data["hops"][0]["failover_capable"])

    def test_an_unsatisfiable_slot_is_a_409_with_the_reason(self):
        make_proxy("203.0.113.40", country="DE")
        chain = self.saved_chain(self.paid_user, {"country": "FR"})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        error = response.data["error"]
        self.assertEqual(error["code"], "chain_unresolvable")
        self.assertEqual(error["detail"]["code"], "hop_unsatisfied")
        self.assertEqual(error["detail"]["position"], 0)
        self.assertEqual(error["detail"]["constraints"]["country"], "FR")
        self.assertTrue(error["detail"]["candidates"])

    def test_a_dead_pinned_hop_is_a_409_naming_the_position(self):
        dead = make_proxy("203.0.113.50", is_alive=False, consecutive_failures=5)
        chain = self.saved_chain(self.paid_user, {"proxy": dead})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["detail"]["code"], "hop_proxy_dead")
        self.assertIn("hint", response.data["error"]["detail"])

    def test_an_entitlement_failure_reuses_the_phase_1_error_code(self):
        """A client already handling `proxy_not_entitled` from the write
        path needs no new branch."""
        premium = make_proxy("203.0.113.60", tier=ProxyTier.PREMIUM)
        chain = self.saved_chain(self.free_user, {"proxy": premium})
        response = self.as_free().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "proxy_not_entitled")

    def test_a_shrunken_plan_fails_with_chain_length_exceeded(self):
        for index in range(3):
            make_proxy(f"203.0.113.7{index}", tier=ProxyTier.FREE)
        chain = self.saved_chain(self.paid_user, {}, {})
        Subscription.objects.filter(user=self.paid_user).update(status="expired")
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "chain_length_exceeded")

    def test_an_empty_chain_is_a_409(self):
        chain = ProxyChain.objects.create(user=self.paid_user, name="empty")
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["detail"]["code"], "chain_empty")

    def test_another_users_chain_is_not_resolvable(self):
        make_proxy("203.0.113.80")
        chain = self.saved_chain(self.paid_user, {"country": "NL"})
        response = self.as_free().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_resolution_requires_authentication(self):
        make_proxy("203.0.113.90")
        chain = self.saved_chain(self.paid_user, {"country": "NL"})
        self.client.force_authenticate(None)
        response = self.client.post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_resolution_reflects_health_changing_between_calls(self):
        """The failover behaviour, end to end: the best hop dies and the
        next call returns a different proxy without the chain being
        edited."""
        best = make_proxy("203.0.113.100", quality_score=95.0)
        backup = make_proxy("203.0.113.101", quality_score=60.0)
        chain = self.saved_chain(self.paid_user, {"country": "NL"})

        first = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(first.data["hops"][0]["proxy"]["id"], best.pk)

        Proxy.objects.filter(pk=best.pk).update(is_alive=False)

        second = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(second.data["hops"][0]["proxy"]["id"], backup.pk)


class UnprobedHopApiTests(HealthApiTestCase):
    """P15 through the HTTP layer: the error envelope and the write path.

    The resolver's own behaviour is pinned in
    `proxies/test_resolution_unprobed.py`. What matters here is that the
    new failure reaches a client in the *existing* envelope -- a client
    already handling `chain_unresolvable` needs no new branch -- and that
    the opt-in can actually be set through the API rather than only in
    the database.
    """

    def saved_chain(self, user, *hop_kwargs, name="route"):
        chain = ProxyChain.objects.create(user=user, name=name)
        for position, kwargs in enumerate(hop_kwargs):
            ChainHop.objects.create(chain=chain, position=position, **kwargs)
        return chain

    def unprobed(self, host, **kwargs):
        defaults = {
            "probe_count": 0,
            "success_rate": None,
            "latency_p95_ms": None,
            "latency_p50_ms": None,
            "quality_score": 50.0,
            "last_checked": None,
        }
        defaults.update(kwargs)
        return make_proxy(host, **defaults)

    def test_an_unprobed_only_failure_keeps_the_existing_envelope(self):
        self.unprobed("203.0.113.60")
        chain = self.saved_chain(self.paid_user, {"max_latency_ms": 200})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        error = response.data["error"]
        # Unchanged outer code: no client branch has to be added.
        self.assertEqual(error["code"], "chain_unresolvable")
        # New inner code, which is where the detail belongs.
        self.assertEqual(error["detail"]["code"], "hop_unsatisfied_unprobed")
        self.assertEqual(error["detail"]["position"], 0)

    def test_the_response_names_the_remedy(self):
        self.unprobed("203.0.113.61")
        self.unprobed("203.0.113.62")
        chain = self.saved_chain(self.paid_user, {"max_latency_ms": 200})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")

        detail = response.data["error"]["detail"]
        self.assertEqual(detail["unprobed_candidates"], 2)
        self.assertEqual(detail["remedy"]["command"], "probe_proxies")
        self.assertIn("probe_proxies", detail["message"])

    def test_the_stage_counts_are_in_the_response(self):
        """An operator reading the 409 can tell the two causes apart
        without opening a shell."""
        make_proxy("203.0.113.63", latency_p95_ms=900, probe_count=50)
        self.unprobed("203.0.113.64")
        chain = self.saved_chain(self.paid_user, {"max_latency_ms": 200})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")

        stage = [
            row
            for row in response.data["error"]["detail"]["candidates"]
            if row["stage"] == "max_latency_ms"
        ][0]
        self.assertEqual(stage["excluded_measured"], 1)
        self.assertEqual(stage["excluded_unprobed"], 1)

    def test_a_measured_too_slow_failure_keeps_the_generic_inner_code(self):
        make_proxy("203.0.113.65", latency_p95_ms=900, probe_count=50)
        chain = self.saved_chain(self.paid_user, {"max_latency_ms": 200})
        response = self.as_paid().post(f"/api/chains/{chain.pk}/resolve/")
        self.assertEqual(
            response.data["error"]["detail"]["code"], "hop_unsatisfied"
        )

    def test_the_opt_in_can_be_set_when_saving_a_chain(self):
        self.unprobed("203.0.113.66")
        response = self.as_paid().post(
            "/api/chains/",
            {
                "name": "bootstrap",
                "hop_specs": [{"max_latency_ms": 200, "allow_unprobed": True}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["hops"][0]["allow_unprobed"])

    def test_a_chain_saved_with_the_opt_in_then_resolves(self):
        fresh = self.unprobed("203.0.113.67")
        created = self.as_paid().post(
            "/api/chains/",
            {
                "name": "bootstrap2",
                "hop_specs": [{"max_latency_ms": 200, "allow_unprobed": True}],
            },
            format="json",
        )
        response = self.as_paid().post(
            f"/api/chains/{created.data['id']}/resolve/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["hops"][0]["proxy"]["id"], fresh.pk)

    def test_the_opt_in_defaults_to_false_when_omitted(self):
        self.unprobed("203.0.113.68")
        response = self.as_paid().post(
            "/api/chains/",
            {"name": "strict", "hop_specs": [{"max_latency_ms": 200}]},
            format="json",
        )
        self.assertFalse(response.data["hops"][0]["allow_unprobed"])

    def test_the_opt_in_is_rejected_on_a_pinned_hop(self):
        """A constraint field on a fixed hop describes a selection that
        never happens, and the serializer says so before the database
        has to."""
        proxy = make_proxy("203.0.113.69")
        response = self.as_paid().post(
            "/api/chains/",
            {
                "name": "bad",
                "hop_specs": [{"proxy": proxy.pk, "allow_unprobed": True}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_pinned_hop_without_the_opt_in_is_still_accepted(self):
        """The guard above must not reject every fixed hop just because
        the field is always present after its serializer default."""
        proxy = make_proxy("203.0.113.70")
        response = self.as_paid().post(
            "/api/chains/",
            {"name": "pinned", "hop_specs": [{"proxy": proxy.pk}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
