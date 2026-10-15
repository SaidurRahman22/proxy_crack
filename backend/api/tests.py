"""Tests for the parts of Phase 1 that must not be wrong.

The focus is the entitlement boundary from `Project/graph.md` section 4: the
server decides what a user may see, and the client is never trusted.
"""

import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.entitlements import resolve_entitlement
from core.tests import raw_credentials
from billing.models import Plan, Subscription, SubscriptionStatus, UsageLog
from core.models import Content, ContentKind
from proxies.importers import CSVImportError, import_proxies_from_csv
from proxies.models import Proxy, ProxyChain, ProxyProtocol, ProxyTier

User = get_user_model()


def make_plan(code, chain_length, tier, free=False):
    return Plan.objects.create(
        code=code,
        name=code.title(),
        max_chain_length=chain_length,
        max_proxy_tier=tier,
        is_free_tier=free,
        duration_days=0 if free else 30,
    )


def make_proxy(host, tier, protocol=ProxyProtocol.SOCKS5, port=1080, **kwargs):
    return Proxy.objects.create(
        protocol=protocol,
        host=host,
        port=port,
        tier=tier,
        username=kwargs.pop("username", "secret-user"),
        password=kwargs.pop("password", "secret-pass"),
        **kwargs,
    )


class EntitlementBaseTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        self.free_plan = make_plan("free", 1, ProxyTier.FREE, free=True)
        self.pro_plan = make_plan("pro", 3, ProxyTier.PREMIUM)
        self.free_user = User.objects.create_user("free@example.com", "Str0ngPassphrase!")
        self.pro_user = User.objects.create_user("pro@example.com", "Str0ngPassphrase!")
        Subscription.objects.create(
            user=self.pro_user,
            plan=self.pro_plan,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.free_proxy = make_proxy("198.51.100.1", ProxyTier.FREE)
        self.premium_a = make_proxy("198.51.100.2", ProxyTier.PREMIUM)
        self.premium_b = make_proxy("198.51.100.3", ProxyTier.PREMIUM)
        self.premium_c = make_proxy("198.51.100.4", ProxyTier.PREMIUM)


class ProxyListScopingTests(EntitlementBaseTestCase):
    def test_requires_authentication(self):
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_free_user_sees_only_free_tier(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tiers = {row["tier"] for row in response.data["results"]}
        self.assertEqual(tiers, {ProxyTier.FREE.value})
        self.assertEqual(response.data["count"], 1)

    def test_paid_user_sees_free_and_premium(self):
        self.client.force_authenticate(self.pro_user)
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.data["count"], 4)

    def test_tier_query_param_cannot_widen_scope(self):
        """A free user asking for tier=premium gets nothing, not a 500 and
        certainly not premium rows."""
        self.client.force_authenticate(self.free_user)
        response = self.client.get("/api/proxies/?tier=premium")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

    def test_list_never_returns_credentials(self):
        self.client.force_authenticate(self.pro_user)
        response = self.client.get("/api/proxies/")
        for row in response.data["results"]:
            self.assertNotIn("username", row)
            self.assertNotIn("password", row)
            self.assertNotIn("ss_method", row)
        self.assertIn("requires_credentials", response.data["results"][0])

    def test_detail_returns_credentials_only_when_entitled(self):
        self.client.force_authenticate(self.pro_user)
        response = self.client.get(f"/api/proxies/{self.premium_a.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["password"], "secret-pass")

    def test_credentials_decrypt_for_the_api_but_are_ciphertext_at_rest(self):
        """The whole point of encryption at rest: usable through the API,
        unreadable in the table."""
        self.client.force_authenticate(self.pro_user)
        response = self.client.get(f"/api/proxies/{self.premium_a.pk}/")
        self.assertEqual(response.data["username"], "secret-user")
        self.assertEqual(response.data["password"], "secret-pass")

        stored = raw_credentials(self.premium_a.pk)
        self.assertNotEqual(stored["username"], "secret-user")
        self.assertNotEqual(stored["password"], "secret-pass")
        self.assertTrue(stored["password"].startswith("gAAAAA"))

    def test_free_user_cannot_read_premium_credentials(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.get(f"/api/proxies/{self.premium_a.pk}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_disabled_proxy_is_never_served(self):
        self.free_proxy.is_enabled = False
        self.free_proxy.save(update_fields=["is_enabled"])
        self.client.force_authenticate(self.free_user)
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.data["count"], 0)

    def test_list_is_paginated(self):
        self.client.force_authenticate(self.pro_user)
        response = self.client.get("/api/proxies/")
        for key in ("count", "next", "previous", "results"):
            self.assertIn(key, response.data)


class ChainEntitlementTests(EntitlementBaseTestCase):
    def test_free_user_cannot_exceed_chain_length(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.post(
            "/api/chains/",
            {"name": "too long", "proxy_ids": [self.free_proxy.pk, self.premium_a.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "chain_length_exceeded")
        self.assertEqual(ProxyChain.objects.count(), 0)

    def test_free_user_cannot_use_premium_proxy(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.post(
            "/api/chains/",
            {"name": "premium hop", "proxy_ids": [self.premium_a.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "proxy_not_entitled")

    def test_free_user_single_free_hop_is_allowed(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.post(
            "/api/chains/",
            {"name": "my chain", "proxy_ids": [self.free_proxy.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["length"], 1)
        self.assertEqual(response.data["hops"][0]["position"], 0)

    def test_paid_user_three_hops_in_order(self):
        self.client.force_authenticate(self.pro_user)
        ordered = [self.premium_c.pk, self.free_proxy.pk, self.premium_a.pk]
        response = self.client.post(
            "/api/chains/", {"name": "triple", "proxy_ids": ordered}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual([hop["proxy"]["id"] for hop in response.data["hops"]], ordered)

    def test_paid_user_cannot_exceed_their_own_limit(self):
        self.client.force_authenticate(self.pro_user)
        response = self.client.post(
            "/api/chains/",
            {
                "name": "four",
                "proxy_ids": [
                    self.premium_a.pk,
                    self.premium_b.pk,
                    self.premium_c.pk,
                    self.free_proxy.pk,
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "chain_length_exceeded")

    def test_chain_length_revalidated_on_update(self):
        self.client.force_authenticate(self.free_user)
        created = self.client.post(
            "/api/chains/",
            {"name": "mine", "proxy_ids": [self.free_proxy.pk]},
            format="json",
        )
        chain_id = created.data["id"]
        response = self.client.patch(
            f"/api/chains/{chain_id}/",
            {"proxy_ids": [self.free_proxy.pk, self.premium_a.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "chain_length_exceeded")

    def test_duplicate_proxy_rejected(self):
        self.client.force_authenticate(self.pro_user)
        response = self.client.post(
            "/api/chains/",
            {"name": "dupe", "proxy_ids": [self.premium_a.pk, self.premium_a.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_chains_are_private_to_their_owner(self):
        self.client.force_authenticate(self.pro_user)
        created = self.client.post(
            "/api/chains/", {"name": "pro chain", "proxy_ids": [self.premium_a.pk]}, format="json"
        )
        self.client.force_authenticate(self.free_user)
        listed = self.client.get("/api/chains/")
        self.assertEqual(listed.data["count"], 0)
        fetched = self.client.get(f"/api/chains/{created.data['id']}/")
        self.assertEqual(fetched.status_code, status.HTTP_404_NOT_FOUND)


class SubscriptionResolutionTests(EntitlementBaseTestCase):
    def test_expired_subscription_falls_back_to_free(self):
        subscription = Subscription.objects.get(user=self.pro_user)
        subscription.expires_at = timezone.now() - timedelta(days=1)
        subscription.save(update_fields=["expires_at"])

        entitlement = resolve_entitlement(self.pro_user)
        self.assertEqual(entitlement.source, "free_plan")
        self.assertEqual(entitlement.max_chain_length, 1)

        self.client.force_authenticate(self.pro_user)
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.data["count"], 1)

    def test_cancelled_subscription_falls_back_to_free(self):
        subscription = Subscription.objects.get(user=self.pro_user)
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.save(update_fields=["status"])
        self.assertEqual(resolve_entitlement(self.pro_user).max_chain_length, 1)

    def test_no_free_plan_row_falls_back_to_settings_not_open(self):
        Plan.objects.filter(is_free_tier=True).delete()
        entitlement = resolve_entitlement(self.free_user)
        self.assertEqual(entitlement.source, "fallback")
        self.assertEqual(entitlement.max_chain_length, 1)
        self.assertEqual(entitlement.allowed_proxy_tiers, ("free",))

    def test_subscription_endpoint_reports_limits(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.get("/api/subscription/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plan"]["code"], "free")
        self.assertEqual(response.data["entitlements"]["max_chain_length"], 1)
        self.assertEqual(response.data["entitlements"]["allowed_proxy_tiers"], ["free"])

    def test_bandwidth_usage_is_reported(self):
        UsageLog.objects.create(
            user=self.free_user,
            bytes_up=1000,
            bytes_down=2000,
            session_start=timezone.now() - timedelta(hours=1),
            session_end=timezone.now(),
        )
        self.client.force_authenticate(self.free_user)
        response = self.client.get("/api/subscription/")
        self.assertEqual(response.data["entitlements"]["bandwidth_used_bytes"], 3000)


class AuthFlowTests(APITestCase):
    def setUp(self):
        cache.clear()
        make_plan("free", 1, ProxyTier.FREE, free=True)

    def test_register_then_login_then_refresh(self):
        register = self.client.post(
            "/api/auth/register/",
            {
                "email": "New.User@Example.com",
                "password": "Str0ngPassphrase!",
                "password_confirm": "Str0ngPassphrase!",
            },
            format="json",
        )
        self.assertEqual(register.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", register.data)
        self.assertEqual(register.data["user"]["email"], "new.user@example.com")

        login = self.client.post(
            "/api/auth/login/",
            {"email": "new.user@example.com", "password": "Str0ngPassphrase!"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

        refresh = self.client.post(
            "/api/auth/refresh/", {"refresh": login.data["refresh"]}, format="json"
        )
        self.assertEqual(refresh.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh.data)

    def test_weak_password_rejected(self):
        response = self.client.post(
            "/api/auth/register/",
            {"email": "weak@example.com", "password": "password", "password_confirm": "password"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_email_rejected(self):
        User.objects.create_user("taken@example.com", "Str0ngPassphrase!")
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "TAKEN@example.com",
                "password": "Str0ngPassphrase!",
                "password_confirm": "Str0ngPassphrase!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UsageReportTests(EntitlementBaseTestCase):
    def test_usage_is_attributed_to_the_token_user(self):
        self.client.force_authenticate(self.free_user)
        response = self.client.post(
            "/api/usage/",
            {
                "user": self.pro_user.pk,  # ignored on purpose
                "bytes_up": 100,
                "bytes_down": 200,
                "session_start": (timezone.now() - timedelta(minutes=5)).isoformat(),
                "session_end": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        log = UsageLog.objects.get()
        self.assertEqual(log.user, self.free_user)

    def test_session_end_before_start_rejected(self):
        self.client.force_authenticate(self.free_user)
        now = timezone.now()
        response = self.client.post(
            "/api/usage/",
            {
                "bytes_up": 1,
                "bytes_down": 1,
                "session_start": now.isoformat(),
                "session_end": (now - timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ContentTests(APITestCase):
    def setUp(self):
        cache.clear()
        Content.objects.create(
            key="welcome", kind=ContentKind.ANNOUNCEMENT, title="Hello", is_published=True
        )
        Content.objects.create(
            key="draft", kind=ContentKind.ANNOUNCEMENT, title="Draft", is_published=False
        )
        Content.objects.create(
            key="tos-notice", kind=ContentKind.NOTICE, title="Notice", is_published=True
        )

    def test_announcements_exclude_unpublished_and_other_kinds(self):
        response = self.client.get("/api/content/announcements/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        keys = [row["key"] for row in response.data["results"]]
        self.assertEqual(keys, ["welcome"])


class CSVImportTests(APITestCase):
    def test_counts_successes_and_failures(self):
        csv_text = (
            "protocol,host,port,username,password,ss_method,country,city,tier\n"
            "socks5,198.51.100.10,1080,alice,s3cret,,NL,Amsterdam,premium\n"
            "http,203.0.113.7,8080,,,,DE,Frankfurt,free\n"
            "ss,198.51.100.22,8388,,hunter2,aes-256-gcm,SG,Singapore,premium\n"
            "ss,198.51.100.23,8388,,hunter2,,SG,Singapore,premium\n"  # no cipher
            "socks5,198.51.100.11,99999,,,,NL,,free\n"  # bad port
            "gopher,198.51.100.12,70,,,,NL,,free\n"  # bad protocol
        )
        batch = import_proxies_from_csv(io.BytesIO(csv_text.encode()), "seed.csv")
        self.assertEqual(batch.row_count, 6)
        self.assertEqual(batch.success_count, 3)
        self.assertEqual(batch.created_count, 3)
        self.assertEqual(batch.fail_count, 3)
        self.assertEqual(Proxy.objects.count(), 3)
        self.assertIn("ss_method", batch.errors)

    def test_reupload_updates_instead_of_duplicating(self):
        first = "protocol,host,port,password,ss_method\nss,198.51.100.30,8388,old,aes-256-gcm\n"
        second = "protocol,host,port,password,ss_method\nss,198.51.100.30,8388,new,aes-256-gcm\n"
        import_proxies_from_csv(io.BytesIO(first.encode()), "a.csv")
        batch = import_proxies_from_csv(io.BytesIO(second.encode()), "b.csv")
        self.assertEqual(Proxy.objects.count(), 1)
        self.assertEqual(batch.updated_count, 1)
        self.assertEqual(Proxy.objects.get().password, "new")

    def test_tier_defaults_to_premium_when_column_missing(self):
        csv_text = "protocol,host,port\nsocks5,198.51.100.40,1080\n"
        import_proxies_from_csv(io.BytesIO(csv_text.encode()), "c.csv")
        self.assertEqual(Proxy.objects.get().tier, ProxyTier.PREMIUM)

    def test_missing_required_column_rejects_the_file(self):
        with self.assertRaises(CSVImportError):
            import_proxies_from_csv(io.BytesIO(b"protocol,host\nsocks5,1.2.3.4\n"), "d.csv")

    def test_imported_credentials_are_encrypted_at_rest(self):
        csv_text = (
            "protocol,host,port,username,password,ss_method\n"
            "ss,198.51.100.50,8388,alice,s3cret,aes-256-gcm\n"
        )
        import_proxies_from_csv(io.BytesIO(csv_text.encode()), "e.csv")
        proxy = Proxy.objects.get(host="198.51.100.50")
        # Readable through the ORM...
        self.assertEqual(proxy.username, "alice")
        self.assertEqual(proxy.password, "s3cret")
        self.assertEqual(proxy.ss_method, "aes-256-gcm")
        # ...ciphertext in the table.
        stored = raw_credentials(proxy.pk)
        self.assertNotIn("s3cret", "".join(stored.values()))
        self.assertNotIn("alice", "".join(stored.values()))

    def test_reupload_rewrites_encrypted_credentials(self):
        head = "protocol,host,port,password,ss_method\n"
        import_proxies_from_csv(
            io.BytesIO((head + "ss,198.51.100.51,8388,old,aes-256-gcm\n").encode()), "f.csv"
        )
        import_proxies_from_csv(
            io.BytesIO((head + "ss,198.51.100.51,8388,new,aes-256-gcm\n").encode()), "g.csv"
        )
        proxy = Proxy.objects.get(host="198.51.100.51")
        self.assertEqual(proxy.password, "new")
        self.assertNotIn("new", raw_credentials(proxy.pk)["password"])


class AdminSmokeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_superuser("admin@example.com", "Str0ngPassphrase!")
        self.client.force_login(self.staff)

    def test_csv_upload_page_renders(self):
        response = self.client.get(reverse("admin:proxies_proxy_upload_csv"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_csv_upload_creates_batch_and_proxies(self):
        upload = io.BytesIO(b"protocol,host,port,tier\nhttp,203.0.113.50,3128,free\n")
        upload.name = "batch.csv"
        response = self.client.post(
            reverse("admin:proxies_proxy_upload_csv"), {"csv_file": upload}, follow=True
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Proxy.objects.count(), 1)
        self.assertEqual(Proxy.objects.get().upload_batch.success_count, 1)

    def test_proxy_changelist_renders(self):
        response = self.client.get(reverse("admin:proxies_proxy_changelist"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_change_form_round_trips_an_encrypted_credential(self):
        proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.90",
            port=1080,
            tier=ProxyTier.PREMIUM,
            username="alice",
            password="s3cret",
        )
        url = reverse("admin:proxies_proxy_change", args=[proxy.pk])
        rendered = self.client.get(url)
        self.assertEqual(rendered.status_code, status.HTTP_200_OK)
        # The operator sees plaintext in the form, and the browser is told
        # the plaintext limit rather than the ciphertext width.
        self.assertContains(rendered, 'value="alice"')
        self.assertContains(rendered, 'maxlength="255"')

        posted = self.client.post(
            url,
            {
                "protocol": ProxyProtocol.SOCKS5,
                "host": "198.51.100.90",
                "port": "1080",
                "username": "alice",
                "password": "changed-in-admin",
                "ss_method": "",
                "country": "NL",
                "city": "",
                "tier": ProxyTier.PREMIUM,
                "is_enabled": "on",
                "is_alive": "on",
                "latency_ms": "",
                "last_checked_0": "",
                "last_checked_1": "",
                "chain_hops-TOTAL_FORMS": "0",
                "chain_hops-INITIAL_FORMS": "0",
            },
            follow=True,
        )
        self.assertEqual(posted.status_code, status.HTTP_200_OK)
        proxy.refresh_from_db()
        self.assertEqual(proxy.password, "changed-in-admin")
        self.assertNotIn("changed-in-admin", raw_credentials(proxy.pk)["password"])
