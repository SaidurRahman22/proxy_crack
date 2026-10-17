"""Mobile API: chain diversity, for the warning shown before connecting.

Two endpoints, two jobs: `GET /api/chains/{id}/diversity/` scores a saved
chain, and `POST /api/chains/diversity/` scores one the user is still
assembling.

Three properties are asserted repeatedly here because each would be a
quiet failure rather than a loud one:

* **No lookups.** The read path reads cache rows and nothing else. A
  client opening a screen must not be able to make this server emit DNS.
* **No vendor names.** The user learns that two hops came from one vendor,
  never which vendor -- publishing the operator's supply chain to every
  subscriber is a live commercial risk (P4).
* **No credentials.** Same boundary as `/resolve/`.

Nothing here touches the network; intel rows are written directly.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Plan, Subscription
from proxies.models import (
    ChainHop,
    IpIntelRecord,
    IpIntelStatus,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
)

User = get_user_model()
PASSWORD = "Str0ngPassphrase!"

CREDENTIAL_KEYS = ("username", "password", "ss_method")


def write_intel(host, *, asn, org, prefix, country, fresh=True):
    now = timezone.now()
    return IpIntelRecord.objects.create(
        query=host.lower(),
        ip=host,
        asn=asn,
        asn_org=org,
        prefix=prefix,
        country=country,
        registry="ripencc",
        status=IpIntelStatus.OK,
        source="fixture",
        looked_up_at=now - timedelta(hours=1),
        expires_at=now + (timedelta(days=29) if fresh else -timedelta(hours=1)),
    )


class DiversityApiTestCase(APITestCase):
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
        self.user = User.objects.create_user("paid@example.com", PASSWORD)
        self.other = User.objects.create_user("other@example.com", PASSWORD)
        self.free_user = User.objects.create_user("free@example.com", PASSWORD)
        for user in (self.user, self.other):
            Subscription.objects.create(
                user=user,
                plan=self.pro_plan,
                expires_at=timezone.now() + timedelta(days=30),
            )

        # Hops 0 and 2 share AS64500 and a vendor; hop 1 is elsewhere.
        self.entry = self.make_proxy("10.0.0.1", provider="Webshare", country="NL")
        self.middle = self.make_proxy("172.16.0.1", provider="Bright Data", country="BR")
        self.exit = self.make_proxy("10.9.0.1", provider="Webshare", country="DE")
        write_intel("10.0.0.1", asn=64500, org="Acme", prefix="10.0.0.0/16", country="NL")
        write_intel("172.16.0.1", asn=64501, org="Beta", prefix="172.16.0.0/16", country="BR")
        write_intel("10.9.0.1", asn=64500, org="Acme", prefix="10.9.0.0/16", country="DE")

    def make_proxy(self, host, **kwargs):
        defaults = {
            "protocol": ProxyProtocol.SOCKS5,
            "port": 1080,
            "tier": ProxyTier.PREMIUM,
            "username": "paid-user",
            "password": "paid-pass",
        }
        defaults.update(kwargs)
        return Proxy.objects.create(host=host, **defaults)

    def make_chain(self, user=None, proxies=None, name="chain"):
        chain = ProxyChain.objects.create(user=user or self.user, name=name)
        for index, proxy in enumerate(proxies or [self.entry, self.middle, self.exit]):
            ChainHop.objects.create(chain=chain, proxy=proxy, position=index)
        return chain

    def as_user(self, user=None):
        self.client.force_authenticate(user or self.user)
        return self.client

    @staticmethod
    def codes(payload):
        return [finding["code"] for finding in payload["findings"]]


class SavedChainDiversityTests(DiversityApiTestCase):
    def url(self, chain):
        return f"/api/chains/{chain.pk}/diversity/"

    def test_it_names_the_entry_exit_correlation(self):
        chain = self.make_chain()

        response = self.as_user().get(self.url(chain))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data
        self.assertIn("entry_exit_shared_asn", self.codes(payload))
        self.assertEqual(payload["worst_severity"], "critical")
        self.assertLess(payload["score"], 50)
        self.assertEqual(payload["chain"]["id"], chain.pk)

    def test_the_payload_shape_the_client_renders(self):
        chain = self.make_chain()

        payload = self.as_user().get(self.url(chain)).data

        for key in (
            "score",
            "worst_severity",
            "summary",
            "vocabulary_note",
            "coverage",
            "counts",
            "hops",
            "findings",
            "resolution",
            "analysed_at",
        ):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["hops"]), 3)
        self.assertEqual(payload["coverage"], {
            "hops": 3,
            "known": 3,
            "unknown": 0,
            "fraction": 1.0,
            "score_ceiling": 100.0,
        })
        first = payload["findings"][0]
        for key in ("code", "severity", "title", "message", "positions", "detail"):
            self.assertIn(key, first)

    def test_vendor_names_are_pseudonymised(self):
        chain = self.make_chain()

        payload = self.as_user().get(self.url(chain)).data
        blob = str(payload)

        self.assertNotIn("Webshare", blob)
        self.assertNotIn("Bright Data", blob)
        self.assertIn("Vendor A", blob)
        # The relationship is still reported -- that is the whole point.
        self.assertIn("entry_exit_shared_provider", self.codes(payload))

    def test_no_credentials_anywhere_in_the_response(self):
        chain = self.make_chain()

        blob = str(self.as_user().get(self.url(chain)).data)

        for key in CREDENTIAL_KEYS:
            self.assertNotIn(key, blob)
        self.assertNotIn("paid-pass", blob)

    def test_it_performs_no_lookups(self):
        """A chain of hosts with no cached intel comes back as unknown and
        writes nothing. If this endpoint ever learns to look things up, a
        client could drive outbound DNS by opening a screen."""
        unknown_a = self.make_proxy("203.0.113.5", provider="A", country="NL")
        unknown_b = self.make_proxy("203.0.113.6", provider="B", country="BR")
        chain = self.make_chain(proxies=[unknown_a, unknown_b], name="unknown")
        before = IpIntelRecord.objects.count()

        payload = self.as_user().get(self.url(chain)).data

        self.assertEqual(IpIntelRecord.objects.count(), before)
        self.assertIn("hop_intel_unknown", self.codes(payload))
        self.assertEqual(payload["worst_severity"], "unknown")
        self.assertEqual(payload["coverage"]["known"], 0)

    def test_an_unknown_chain_does_not_score_as_a_clean_one(self):
        unknown_a = self.make_proxy("203.0.113.5", provider="A", country="NL")
        unknown_b = self.make_proxy("203.0.113.6", provider="B", country="BR")
        chain = self.make_chain(proxies=[unknown_a, unknown_b], name="unknown")

        payload = self.as_user().get(self.url(chain)).data

        self.assertEqual(payload["score"], 50.0)
        self.assertEqual(payload["coverage"]["score_ceiling"], 50.0)

    def test_a_single_hop_chain_is_reported_honestly(self):
        chain = self.make_chain(proxies=[self.entry], name="one")

        payload = self.as_user().get(self.url(chain)).data

        self.assertIn("single_hop", self.codes(payload))
        self.assertEqual(payload["score"], 0.0)
        self.assertIn("not a chain", str(payload))

    def test_a_constrained_hop_that_resolves_is_analysed_as_resolved(self):
        chain = ProxyChain.objects.create(user=self.user, name="constrained")
        ChainHop.objects.create(chain=chain, proxy=self.entry, position=0)
        ChainHop.objects.create(chain=chain, position=1, country="DE")

        payload = self.as_user().get(self.url(chain)).data

        self.assertTrue(payload["resolution"]["resolved"])
        self.assertIsNone(payload["resolution"]["error"])
        self.assertNotIn("hop_unresolved", self.codes(payload))
        # The resolver picked 10.9.0.1 for "DE", which shares AS64500 with
        # the fixed entry hop -- exactly the warning the builder needs.
        self.assertIn("entry_exit_shared_asn", self.codes(payload))

    def test_an_unresolvable_chain_still_returns_an_analysis(self):
        """A 409 would be the wrong answer to "warn me about this chain".
        `POST /resolve/` is where a client finds out it cannot connect."""
        chain = ProxyChain.objects.create(user=self.user, name="impossible")
        ChainHop.objects.create(chain=chain, proxy=self.entry, position=0)
        ChainHop.objects.create(chain=chain, position=1, country="ZZ")

        response = self.as_user().get(self.url(chain))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["resolution"]["resolved"])
        self.assertEqual(response.data["resolution"]["error"]["position"], 1)
        self.assertIn("hop_unresolved", self.codes(response.data))

    def test_another_users_chain_is_not_readable(self):
        chain = self.make_chain(user=self.other, name="theirs")

        response = self.as_user().get(self.url(chain))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_authentication_is_required(self):
        chain = self.make_chain()
        self.client.force_authenticate(None)

        response = self.client.get(self.url(chain))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AdHocDiversityTests(DiversityApiTestCase):
    URL = "/api/chains/diversity/"

    def test_it_scores_a_chain_that_was_never_saved(self):
        response = self.as_user().post(
            self.URL,
            {"proxy_ids": [self.entry.pk, self.middle.pk, self.exit.pk]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("entry_exit_shared_asn", self.codes(response.data))
        self.assertEqual(ProxyChain.objects.count(), 0)

    def test_order_is_respected_so_entry_and_exit_mean_something(self):
        """The same three proxies, reordered so the related pair is no
        longer first-and-last, must stop being critical."""
        critical = self.as_user().post(
            self.URL,
            {"proxy_ids": [self.entry.pk, self.middle.pk, self.exit.pk]},
            format="json",
        ).data
        reordered = self.as_user().post(
            self.URL,
            {"proxy_ids": [self.entry.pk, self.exit.pk, self.middle.pk]},
            format="json",
        ).data

        self.assertEqual(critical["worst_severity"], "critical")
        self.assertEqual(reordered["worst_severity"], "medium")
        self.assertGreater(reordered["score"], critical["score"])

    def test_the_same_proxy_twice_is_not_silently_collapsed(self):
        response = self.as_user().post(
            self.URL, {"proxy_ids": [self.entry.pk, self.entry.pk]}, format="json"
        )

        self.assertEqual(len(response.data["hops"]), 2)
        self.assertEqual(response.data["worst_severity"], "critical")

    def test_entitlement_is_enforced_on_ids_in_the_body(self):
        """`get_queryset` scoping does not reach a list posted in a body,
        so the endpoint would otherwise be an inventory oracle."""
        self.client.force_authenticate(self.free_user)

        response = self.client.post(
            self.URL, {"proxy_ids": [self.entry.pk]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "proxy_not_entitled")

    def test_an_unknown_id_is_refused(self):
        response = self.as_user().post(
            self.URL, {"proxy_ids": [999999]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_list_is_capped(self):
        response = self.as_user().post(
            self.URL, {"proxy_ids": list(range(1, 50))}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_empty_list_is_refused(self):
        response = self.as_user().post(self.URL, {"proxy_ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authentication_is_required(self):
        self.client.force_authenticate(None)
        response = self.client.post(
            self.URL, {"proxy_ids": [self.entry.pk]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_vendor_names_are_pseudonymised_here_too(self):
        payload = self.as_user().post(
            self.URL,
            {"proxy_ids": [self.entry.pk, self.middle.pk, self.exit.pk]},
            format="json",
        ).data

        self.assertNotIn("Webshare", str(payload))
        self.assertIn("Vendor A", str(payload))

    def test_the_error_envelope_is_unchanged(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.post(
            self.URL, {"proxy_ids": [self.entry.pk]}, format="json"
        )
        self.assertIn("error", response.data)
        for key in ("code", "status", "detail"):
            self.assertIn(key, response.data["error"])
