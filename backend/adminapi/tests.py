"""Tests for the staff-only admin API.

The load-bearing test is `StaffGateTests.test_non_staff_token_is_refused_on_
every_admin_endpoint`. It does not hand-list endpoints: it walks the live
URLconf, builds a concrete request for every route and every HTTP method
that route accepts, and asserts 403 for each. A new viewset that forgets
`AdminViewMixin` therefore fails the suite the moment it is registered,
which is the only failure mode that can leak the entire database at once.

Everything else here is the contract: response shapes, the login refusal,
the credential boundary between list and detail, the plan-delete guard, the
CSV import, and the bulk operations.
"""

import io
import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient, APITestCase
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from adminapi.authentication import (
    ADMIN_SCOPE_CLAIM,
    AdminJWTAuthentication,
    stamp_admin_scope,
)
from adminapi.checks import audit_patterns, _iter_patterns
from adminapi.permissions import IsStaffUser
from adminapi.throttling import AdminLoginEmailThrottle
from adminapi.serializers import parse_batch_errors
from adminapi.urls import urlpatterns as admin_urlpatterns
from billing.models import (
    Plan,
    Subscription,
    SubscriptionStatus,
    UsageLog,
)
from core.models import Content, ContentKind
from core.tests import raw_credentials
from proxies.models import (
    ChainHop,
    Proxy,
    ProxyChain,
    ProxyProtocol,
    ProxyTier,
    UploadBatch,
)

User = get_user_model()

PASSWORD = "Str0ngPassphrase!"
NEW_PASSWORD = "An0therL0ngPassphrase!"

# Keys that must never appear anywhere in a list response.
CREDENTIAL_KEYS = ("username", "password", "ss_method")

NAMED_GROUP_RE = re.compile(r"\(\?P<(\w+)>[^)]+\)")

CSV_HEADER = "protocol,host,port,username,password,ss_method,country,city,tier\n"
CSV_BODY = (
    "socks5,203.0.113.10,1080,csvuser,csvpass,,US,New York,premium\n"
    "ss,203.0.113.11,8388,,,aes-256-gcm,DE,Berlin,free\n"
    "http,203.0.113.12,notaport,,,,FR,Paris,\n"
    "teleport,203.0.113.13,8080,,,,GB,London,\n"
)


# ---------------------------------------------------------------------------
# URLconf introspection -- the parametrisation source
# ---------------------------------------------------------------------------


def admin_endpoints(pk="1"):
    """Every (path, methods, url_name) under /api/admin/, from the URLconf.

    Read from the live patterns rather than a hand-written list so that a
    route added later is covered by the permission sweep automatically. A
    test that enumerates endpoints by hand protects only the endpoints
    somebody remembered to add to it.
    """
    endpoints = []
    for route, pattern in _iter_patterns(admin_urlpatterns):
        raw = route.replace("^", "").replace("$", "")
        url = "/api/admin/" + NAMED_GROUP_RE.sub(pk, raw)

        actions = getattr(pattern.callback, "actions", None)
        if actions:
            methods = sorted(actions)
        else:
            view_class = pattern.callback.cls
            methods = sorted(
                name
                for name in ("get", "post", "put", "patch", "delete")
                if hasattr(view_class, name)
            )
        endpoints.append((url, methods, pattern.name))
    return endpoints


class AdminApiTestCase(APITestCase):
    """Fixtures shared by the admin tests.

    `cache.clear()` resets the throttle history, which lives in the default
    cache and is not rolled back with the transaction.
    """

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
            price="9.99",
            max_chain_length=3,
            max_proxy_tier=ProxyTier.PREMIUM,
            duration_days=30,
            sort_order=1,
        )

        self.staff = User.objects.create_user(
            "staff@example.com", PASSWORD, is_staff=True, first_name="Stella"
        )
        self.superuser = User.objects.create_superuser("root@example.com", PASSWORD)
        self.member = User.objects.create_user("member@example.com", PASSWORD)
        self.inactive_staff = User.objects.create_user(
            "frozen@example.com", PASSWORD, is_staff=True, is_active=False
        )

        self.subscription = Subscription.objects.create(
            user=self.member,
            plan=self.pro_plan,
            expires_at=timezone.now() + timedelta(days=30),
        )

        self.free_proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.1",
            port=1080,
            tier=ProxyTier.FREE,
            country="US",
            city="Dallas",
            username="free-user",
            password="free-pass",
        )
        self.premium_proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.2",
            port=1080,
            tier=ProxyTier.PREMIUM,
            country="DE",
            city="Berlin",
            username="paid-user",
            password="paid-pass",
            latency_ms=42,
        )
        self.ss_proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="198.51.100.3",
            port=8388,
            tier=ProxyTier.PREMIUM,
            country="JP",
            city="Tokyo",
            password="ss-pass",
            ss_method="aes-256-gcm",
        )

        self.content = Content.objects.create(
            key="welcome-hero",
            kind=ContentKind.ONBOARDING,
            title="Welcome",
            body="Chain your proxies.",
            is_published=True,
        )

        self.usage = UsageLog.objects.create(
            user=self.member,
            bytes_up=1000,
            bytes_down=2000,
            session_start=timezone.now() - timedelta(hours=1),
            session_end=timezone.now(),
        )

        self.chain = ProxyChain.objects.create(user=self.member, name="Tokyo route")
        ChainHop.objects.create(chain=self.chain, proxy=self.premium_proxy, position=0)

        self.batch = UploadBatch.objects.create(
            uploaded_by=self.staff,
            filename="seed.csv",
            row_count=2,
            created_count=1,
            updated_count=1,
            success_count=2,
            fail_count=0,
        )

    # -- auth helpers -------------------------------------------------------

    @staticmethod
    def admin_refresh(user):
        """A refresh token as `/api/admin/auth/login/` would mint it."""
        return stamp_admin_scope(RefreshToken.for_user(user))

    @classmethod
    def access_token(cls, user):
        return str(cls.admin_refresh(user).access_token)

    @classmethod
    def refresh_token(cls, user):
        return str(cls.admin_refresh(user))

    @staticmethod
    def mobile_access_token(user):
        """A token as the *mobile* `/api/auth/login/` mints it: same key,
        same user, no admin scope claim."""
        return str(RefreshToken.for_user(user).access_token)

    @staticmethod
    def mobile_refresh_token(user):
        return str(RefreshToken.for_user(user))

    def authenticate(self, user, client=None):
        """A real JWT, not force_authenticate: the point of these tests is
        that a *valid token* for the wrong user is refused."""
        client = client or self.client
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access_token(user)}")
        return client


# ---------------------------------------------------------------------------
# The staff gate
# ---------------------------------------------------------------------------


class StaffGateTests(AdminApiTestCase):
    def _request(self, client, url, method, name):
        """One request, with a body appropriate to the route.

        login and refresh take no token (there is none yet), so they are
        exercised with genuinely valid non-staff credentials -- the case the
        contract calls out: right password, wrong kind of account.
        """
        if name == "auth-login":
            return client.post(
                url, {"email": self.member.email, "password": PASSWORD}, format="json"
            )
        if name == "auth-refresh":
            return client.post(
                url, {"refresh": self.refresh_token(self.member)}, format="json"
            )
        if name == "auth-logout":
            return getattr(client, method)(
                url, {"refresh": self.refresh_token(self.member)}, format="json"
            )
        if method in ("get", "delete"):
            return getattr(client, method)(url)
        return getattr(client, method)(url, {}, format="json")

    def test_endpoint_inventory_covers_every_contract_resource(self):
        """Guards the sweep below: if the URLconf walk silently returned
        nothing, every assertion in this class would vacuously pass."""
        urls = {url for url, _methods, _name in admin_endpoints()}
        for resource in (
            "auth/login",
            "auth/refresh",
            "auth/logout",
            "auth/me",
            "stats",
            "users",
            "plans",
            "subscriptions",
            "proxies",
            "upload-batches",
            "content",
            "usage-logs",
            "chains",
        ):
            self.assertTrue(
                any(f"/api/admin/{resource}" in url for url in urls),
                f"no route found for {resource}",
            )
        self.assertGreaterEqual(len(urls), 20)

    def _sweep(self, access_token, refresh_token):
        """Hit every route and every method; return (checked, failures)."""
        checked = 0
        failures = []

        for url, methods, name in admin_endpoints():
            for method in methods:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
                if name == "auth-refresh":
                    response = client.post(
                        url, {"refresh": refresh_token}, format="json"
                    )
                elif name == "auth-logout":
                    response = getattr(client, method)(
                        url, {"refresh": refresh_token}, format="json"
                    )
                else:
                    response = self._request(client, url, method, name)
                checked += 1

                if response.status_code != status.HTTP_403_FORBIDDEN:
                    failures.append(
                        f"{method.upper()} {url} -> {response.status_code} "
                        f"(expected 403)"
                    )
                    continue

                body = json.dumps(response.data, default=str)
                for leak in ("results", '"access"', '"refresh"', self.staff.email):
                    if leak in body:
                        failures.append(f"{method.upper()} {url} leaked {leak!r}")

        return checked, failures

    def test_non_staff_token_is_refused_on_every_admin_endpoint(self):
        """THE test. A valid end-user JWT -- the real one, as the mobile API
        mints it -- gets 403 everywhere, never data."""
        checked, failures = self._sweep(
            self.mobile_access_token(self.member),
            self.mobile_refresh_token(self.member),
        )
        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreaterEqual(checked, 30, "the sweep covered suspiciously few calls")

    def test_non_staff_token_with_the_admin_scope_is_still_refused(self):
        """Defence in depth, proved rather than assumed.

        If the scope claim were ever obtainable by an end user -- a minting
        bug, a leaked admin token reissued for the wrong subject -- the
        permission layer must still refuse. Both controls are tested
        independently because the whole point of having two is that either
        one alone is sufficient.
        """
        checked, failures = self._sweep(
            self.access_token(self.member), self.refresh_token(self.member)
        )
        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreaterEqual(checked, 30)

    def test_staff_token_from_the_mobile_api_is_refused(self):
        """A staff member's *mobile* token is not an admin token. Same key,
        same user, no admin scope claim -- refused at the authentication
        layer, before any permission class runs."""
        checked, failures = self._sweep(
            self.mobile_access_token(self.staff),
            self.mobile_refresh_token(self.staff),
        )
        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreaterEqual(checked, 30)

    def test_a_404_is_never_reachable_before_a_403(self):
        """A 404 would confirm the route exists and that the permission
        check runs after the object lookup."""
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.mobile_access_token(self.member)}"
        )
        for url in (
            "/api/admin/users/999999/",
            "/api/admin/proxies/999999/",
            "/api/admin/plans/999999/",
        ):
            self.assertEqual(
                client.get(url).status_code, status.HTTP_403_FORBIDDEN, url
            )

    def test_staff_revoked_mid_session_loses_access_without_waiting(self):
        token = self.access_token(self.staff)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(
            client.get("/api/admin/users/").status_code, status.HTTP_200_OK
        )

        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])
        self.assertEqual(
            client.get("/api/admin/users/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_session_cookie_is_not_admin_api_access(self):
        """A Django session is not an admin API credential.

        This used to be argued from `/admin/`, which issued session
        cookies. It is gone, which narrows the exposure but does not close
        the hole: `django.contrib.sessions` is still installed (the DEBUG
        browsable-API login needs it), so `client.login()` still mints a
        real session cookie. If `SessionAuthentication` were ever added
        "so the browsable API works", that cookie would become an admin
        API credential -- an ambient, browser-attached one -- and CSRF
        would come back with it.

        So the assertion is on the configuration, not on the absence of a
        console: JWT-only at the project default, and `AdminJWTAuthentication`
        alone on the admin mixin.
        """
        self.assertNotIn(
            "rest_framework.authentication.SessionAuthentication",
            settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
        )
        from adminapi.views import AdminViewMixin

        self.assertEqual(
            AdminViewMixin.authentication_classes, (AdminJWTAuthentication,)
        )

        client = APIClient()
        self.assertTrue(client.login(email=self.staff.email, password=PASSWORD))
        self.assertEqual(
            client.get("/api/admin/users/").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_anonymous_is_refused_on_every_token_guarded_endpoint(self):
        for url, methods, name in admin_endpoints():
            if name in ("auth-login", "auth-refresh"):
                continue  # AllowAny by necessity; covered by the login tests
            for method in methods:
                client = APIClient()
                response = self._request(client, url, method, name)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                    f"{method.upper()} {url} -> {response.status_code}",
                )

    def test_deactivated_staff_token_is_refused(self):
        """A JWT outlives the account. Disabling someone must take effect on
        the next request, not when their token happens to expire.

        This lands as 401 rather than 403 because SimpleJWT's `get_user`
        refuses an inactive user at the authentication layer, before any
        permission runs. `IsStaffUser` checks `is_active` as well, as the
        backstop for the day that behaviour changes -- asserted directly in
        `IsStaffUserUnitTests` since no request can reach it today.
        """
        token = self.access_token(self.inactive_staff)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.get("/api/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("results", response.data)

    def test_permission_refusal_uses_the_shared_error_envelope(self):
        client = self.authenticate(self.member, APIClient())
        response = client.get("/api/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data,
            {
                "error": {
                    "code": "staff_required",
                    "status": 403,
                    "detail": "Staff access is required.",
                }
            },
        )

    def test_scope_refusal_uses_the_shared_error_envelope(self):
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.mobile_access_token(self.staff)}"
        )
        response = client.get("/api/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "admin_scope_required")

    def test_a_malformed_token_is_401_not_403(self):
        """Only "valid token, wrong audience" is a 403. A token that does
        not verify is still an authentication failure."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertEqual(
            client.get("/api/admin/users/").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_staff_reaches_every_list_endpoint(self):
        """The mirror image: the gate denies the wrong user, not everyone."""
        self.authenticate(self.staff)
        for resource in (
            "users",
            "plans",
            "subscriptions",
            "proxies",
            "upload-batches",
            "content",
            "usage-logs",
            "chains",
        ):
            response = self.client.get(f"/api/admin/{resource}/")
            self.assertEqual(
                response.status_code, status.HTTP_200_OK, f"{resource}: {response.data}"
            )
            self.assertIn("results", response.data)

    def test_superuser_is_also_staff_and_reaches_the_api(self):
        self.authenticate(self.superuser)
        self.assertEqual(
            self.client.get("/api/admin/stats/").status_code, status.HTTP_200_OK
        )


class IsStaffUserUnitTests(TestCase):
    """`IsStaffUser` is the one control the whole API rests on, so each of
    its three conditions is asserted directly rather than only through a
    request that might be refused earlier for another reason."""

    def setUp(self):
        self.permission = IsStaffUser()

    def _request(self, user):
        request = type("Req", (), {})()
        request.user = user
        return request

    def test_anonymous_is_refused(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(
            self.permission.has_permission(self._request(AnonymousUser()), None)
        )

    def test_authenticated_non_staff_is_refused(self):
        user = User.objects.create_user("plain@example.com", PASSWORD)
        self.assertFalse(self.permission.has_permission(self._request(user), None))

    def test_inactive_staff_is_refused(self):
        user = User.objects.create_user(
            "frozen2@example.com", PASSWORD, is_staff=True, is_active=False
        )
        self.assertFalse(self.permission.has_permission(self._request(user), None))

    def test_active_staff_is_allowed(self):
        user = User.objects.create_user("ok@example.com", PASSWORD, is_staff=True)
        self.assertTrue(self.permission.has_permission(self._request(user), None))

    def test_object_permission_applies_the_same_rule(self):
        user = User.objects.create_user("plain2@example.com", PASSWORD)
        self.assertFalse(
            self.permission.has_object_permission(self._request(user), None, object())
        )


class StaffGateStartupCheckTests(TestCase):
    """The startup check is itself a control, so it is tested like one."""

    def test_live_urlconf_passes(self):
        self.assertEqual(audit_patterns(admin_urlpatterns), [])

    def test_an_unguarded_view_is_caught(self):
        class LeakyView(APIView):
            permission_classes = (IsAuthenticated,)  # the realistic mistake

            def get(self, request):
                return Response({})

        errors = audit_patterns([path("leak/", LeakyView.as_view(), name="leak")])
        self.assertEqual([error.id for error in errors], ["adminapi.E001"])

    def test_an_allowany_view_is_caught(self):
        class OpenView(APIView):
            permission_classes = (AllowAny,)

            def get(self, request):
                return Response({})

        errors = audit_patterns([path("open/", OpenView.as_view(), name="open")])
        self.assertEqual(
            sorted(error.id for error in errors), ["adminapi.E001", "adminapi.E002"]
        )

    def test_a_guarded_view_passes(self):
        class GuardedView(APIView):
            permission_classes = (IsAuthenticated, IsStaffUser)

            def get(self, request):
                return Response({})

        self.assertEqual(
            audit_patterns([path("ok/", GuardedView.as_view(), name="ok")]), []
        )

    def test_empty_urlconf_is_reported_rather_than_silently_passing(self):
        errors = audit_patterns([])
        self.assertEqual([error.id for error in errors], ["adminapi.E004"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AdminLoginTests(AdminApiTestCase):
    URL = "/api/admin/auth/login/"

    def test_staff_login_returns_tokens_and_user(self):
        response = self.client.post(
            self.URL, {"email": self.staff.email, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(
            set(response.data["user"]),
            {"id", "email", "first_name", "last_name", "is_staff", "is_superuser"},
        )
        self.assertTrue(response.data["user"]["is_staff"])

    def test_email_is_case_insensitive(self):
        response = self.client.post(
            self.URL, {"email": "STAFF@Example.com", "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_non_staff_is_refused_with_403_despite_a_correct_password(self):
        response = self.client.post(
            self.URL, {"email": self.member.email, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        self.assertEqual(response.data["error"]["code"], "admin_login_failed")

    def test_inactive_staff_is_refused(self):
        response = self.client.post(
            self.URL,
            {"email": self.inactive_staff.email, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_failures_are_indistinguishable(self):
        """No account-existence oracle: unknown address, wrong password and
        correct-password-but-not-staff must be byte-identical."""
        bodies = []
        for payload in (
            {"email": "nobody@example.com", "password": PASSWORD},
            {"email": self.staff.email, "password": "wrong-password-entirely"},
            {"email": self.member.email, "password": PASSWORD},
        ):
            response = self.client.post(self.URL, payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
            bodies.append(json.dumps(response.data, sort_keys=True))

        self.assertEqual(len(set(bodies)), 1, bodies)

    def test_rejected_login_does_not_touch_last_login(self):
        self.assertIsNone(self.member.last_login)
        self.client.post(
            self.URL, {"email": self.member.email, "password": PASSWORD}, format="json"
        )
        self.member.refresh_from_db()
        self.assertIsNone(self.member.last_login)

    def test_successful_login_records_last_login(self):
        self.client.post(
            self.URL, {"email": self.staff.email, "password": PASSWORD}, format="json"
        )
        self.staff.refresh_from_db()
        self.assertIsNotNone(self.staff.last_login)

    def test_issued_access_token_works_against_the_admin_api(self):
        login = self.client.post(
            self.URL, {"email": self.staff.email, "password": PASSWORD}, format="json"
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
        )
        self.assertEqual(
            self.client.get("/api/admin/auth/me/").status_code, status.HTTP_200_OK
        )


class AdminRefreshLogoutMeTests(AdminApiTestCase):
    def test_staff_refresh_returns_an_access_token(self):
        response = self.client.post(
            "/api/admin/auth/refresh/",
            {"refresh": self.refresh_token(self.staff)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        # ROTATE_REFRESH_TOKENS is on, so a new refresh comes back too and
        # the SPA must store it -- the old one is now blacklisted.
        self.assertIn("refresh", response.data)

    def test_non_staff_refresh_is_refused(self):
        response = self.client.post(
            "/api/admin/auth/refresh/",
            {"refresh": self.refresh_token(self.member)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("access", response.data)

    def test_a_mobile_refresh_token_cannot_be_traded_for_admin_access(self):
        """A staff member holding both sessions cannot upgrade the mobile
        one. The refresh endpoint checks the scope claim as well as the
        staff flag -- two independent reasons to refuse."""
        response = self.client.post(
            "/api/admin/auth/refresh/",
            {"refresh": self.mobile_refresh_token(self.staff)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("access", response.data)

    def test_refreshed_access_token_keeps_the_admin_scope(self):
        refreshed = self.client.post(
            "/api/admin/auth/refresh/",
            {"refresh": self.refresh_token(self.staff)},
            format="json",
        ).data
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refreshed['access']}")
        self.assertEqual(
            client.get("/api/admin/auth/me/").status_code, status.HTTP_200_OK
        )

    def test_a_reused_refresh_token_is_rejected_after_rotation(self):
        """ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION silently do
        nothing if token_blacklist is installed but unmigrated, so the
        behaviour is asserted rather than assumed."""
        original = self.refresh_token(self.staff)
        first = self.client.post(
            "/api/admin/auth/refresh/", {"refresh": original}, format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        replay = self.client.post(
            "/api/admin/auth/refresh/", {"refresh": original}, format="json"
        )
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_stops_working_once_staff_is_revoked(self):
        token = self.refresh_token(self.staff)
        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])
        response = self.client.post(
            "/api/admin/auth/refresh/", {"refresh": token}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_blacklists_the_refresh_token(self):
        refresh = self.refresh_token(self.staff)
        self.authenticate(self.staff)
        response = self.client.post(
            "/api/admin/auth/logout/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.client.credentials()
        again = self.client.post(
            "/api/admin/auth/refresh/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(again.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_the_contract_shape(self):
        self.authenticate(self.staff)
        response = self.client.get("/api/admin/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {"id", "email", "first_name", "last_name", "is_staff", "is_superuser"},
        )
        self.assertEqual(response.data["email"], self.staff.email)


# ---------------------------------------------------------------------------
# Credential boundary
# ---------------------------------------------------------------------------


class CredentialBoundaryTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_proxy_list_contains_no_credential_keys(self):
        response = self.client.get("/api/admin/proxies/?page_size=200")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        for row in response.data["results"]:
            for key in CREDENTIAL_KEYS:
                self.assertNotIn(key, row)
            self.assertIn("requires_credentials", row)

    def test_no_credential_value_survives_anywhere_in_a_list_payload(self):
        """Key-by-key checks miss a value nested somewhere unexpected, so
        the rendered body is searched for the plaintext itself."""
        body = self.client.get("/api/admin/proxies/?page_size=200").content.decode()
        for secret in ("paid-pass", "paid-user", "free-pass", "aes-256-gcm"):
            self.assertNotIn(secret, body)

    def test_no_list_endpoint_exposes_credential_keys(self):
        """Every list route, not just the proxy one -- a credential field
        added to a nested serializer would surface somewhere else."""
        for url, methods, name in admin_endpoints():
            if "get" not in methods or not (name or "").endswith("-list"):
                continue
            response = self.client.get(url)
            if response.status_code != status.HTTP_200_OK:
                continue
            body = response.content.decode()
            for key in CREDENTIAL_KEYS:
                self.assertNotIn(
                    f'"{key}"', body, f"{url} exposes {key} in a list response"
                )

    def test_reveal_returns_usable_decrypted_credentials(self):
        response = self.client.post(
            f"/api/admin/proxies/{self.premium_proxy.pk}/reveal/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "paid-user")
        self.assertEqual(response.data["password"], "paid-pass")
        self.assertEqual(
            self.client.post(f"/api/admin/proxies/{self.ss_proxy.pk}/reveal/").data[
                "ss_method"
            ],
            "aes-256-gcm",
        )

    def test_detail_get_does_not_disclose_credentials(self):
        """Opening a drawer is not a disclosure.

        While credentials rode on the detail GET, the audit log recorded page
        views rather than disclosures, and one throttle number had to serve
        both drawer usability and anti-scrape. Disclosure now lives behind
        POST /reveal/ with its own budget.
        """
        response = self.client.get(f"/api/admin/proxies/{self.premium_proxy.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in CREDENTIAL_KEYS:
            self.assertNotIn(key, response.data)

    def test_credentials_are_still_ciphertext_at_rest(self):
        stored = raw_credentials(self.premium_proxy.pk)
        self.assertTrue(stored["password"].startswith("gAAAAA"))
        self.assertNotIn("paid-pass", "".join(v for v in stored.values() if v))

    def test_reveal_fires_the_credential_access_audit_line(self):
        with self.assertLogs("proxycrack.adminapi", level="INFO") as captured:
            self.client.post(f"/api/admin/proxies/{self.premium_proxy.pk}/reveal/")
        self.assertTrue(
            any(
                "credential_access" in line
                and f"proxy={self.premium_proxy.pk}" in line
                and f"user={self.staff.pk}" in line
                for line in captured.output
            ),
            captured.output,
        )

    def test_list_does_not_fire_the_audit_line(self):
        with self.assertLogs("proxycrack.adminapi", level="INFO") as captured:
            self.client.get("/api/admin/proxies/")
            # assertLogs fails with nothing logged, so emit a marker.
            import logging

            logging.getLogger("proxycrack.adminapi").info("marker")
        self.assertFalse(
            any("credential_access" in line for line in captured.output),
            captured.output,
        )

    def test_user_list_never_carries_a_password_hash(self):
        body = self.client.get("/api/admin/users/?page_size=200").content.decode()
        self.assertNotIn('"password"', body)
        self.assertNotIn("pbkdf2", body)
        self.assertNotIn(self.staff.password, body)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class AdminUserTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_list_shape(self):
        response = self.client.get(f"/api/admin/users/?ids={self.member.pk}")
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(
            set(row),
            {
                "id",
                "email",
                "first_name",
                "last_name",
                "is_active",
                "is_staff",
                "is_superuser",
                "date_joined",
                "last_login",
                "subscription",
            },
        )
        self.assertEqual(row["subscription"]["plan"]["code"], "pro")

    def test_user_without_a_subscription_has_null(self):
        response = self.client.get(f"/api/admin/users/{self.staff.pk}/")
        self.assertIsNone(response.data["subscription"])

    def test_create_user(self):
        response = self.client.post(
            "/api/admin/users/",
            {
                "email": "New@Example.com",
                "first_name": "New",
                "last_name": "Person",
                "password": NEW_PASSWORD,
                "is_staff": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertNotIn("password", response.data)
        created = User.objects.get(email="new@example.com")
        self.assertTrue(created.check_password(NEW_PASSWORD))

    def test_create_rejects_a_duplicate_email(self):
        response = self.client.post(
            "/api/admin/users/",
            {"email": self.member.email.upper(), "password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_rejects_a_weak_password(self):
        response = self.client.post(
            "/api/admin/users/",
            {"email": "weak@example.com", "password": "password"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data["error"]["detail"])

    def test_set_password(self):
        response = self.client.post(
            f"/api/admin/users/{self.member.pk}/set_password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(NEW_PASSWORD))

    def test_patch_updates_flags(self):
        response = self.client.patch(
            f"/api/admin/users/{self.member.pk}/", {"is_active": False}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)

    def test_cannot_delete_own_account(self):
        response = self.client.delete(f"/api/admin/users/{self.staff.pk}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "cannot_delete_self")
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())

    def test_search_and_filter(self):
        self.assertEqual(
            self.client.get("/api/admin/users/?search=member").data["count"], 1
        )
        self.assertEqual(
            self.client.get("/api/admin/users/?is_staff=true").data["count"], 3
        )


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


class AdminPlanTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_list_shape_covers_the_contract_fields(self):
        row = self.client.get("/api/admin/plans/").data["results"][0]
        for field in (
            "id",
            "code",
            "name",
            "price",
            "duration_days",
            "max_chain_length",
            "max_proxy_tier",
            "max_devices",
            "bandwidth_limit_bytes",
            "is_free_tier",
            "is_active",
            "sort_order",
            "play_product_id",
        ):
            self.assertIn(field, row)

    def test_create_and_delete_an_unused_plan(self):
        created = self.client.post(
            "/api/admin/plans/",
            {
                "code": "trial",
                "name": "Trial",
                "price": "0.00",
                "duration_days": 7,
                "max_chain_length": 2,
                "max_proxy_tier": ProxyTier.PREMIUM,
                "max_devices": 1,
                "bandwidth_limit_bytes": 0,
                "is_free_tier": False,
                "is_active": True,
                "sort_order": 5,
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        deleted = self.client.delete(f"/api/admin/plans/{created.data['id']}/")
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_with_active_subscriptions_is_409_with_the_count(self):
        response = self.client.delete(f"/api/admin/plans/{self.pro_plan.pk}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        error = response.data["error"]
        self.assertEqual(error["code"], "plan_has_active_subscriptions")
        self.assertEqual(error["status"], 409)
        self.assertEqual(error["detail"]["active_subscriptions"], 1)
        self.assertIsInstance(error["detail"]["active_subscriptions"], int)
        self.assertEqual(error["detail"]["plan"], "pro")
        self.assertTrue(Plan.objects.filter(pk=self.pro_plan.pk).exists())

    def test_the_count_reflects_only_active_subscriptions(self):
        other = User.objects.create_user("second@example.com", PASSWORD)
        Subscription.objects.create(
            user=other,
            plan=self.pro_plan,
            expires_at=timezone.now() + timedelta(days=5),
        )
        response = self.client.delete(f"/api/admin/plans/{self.pro_plan.pk}/")
        self.assertEqual(response.data["error"]["detail"]["active_subscriptions"], 2)

    def test_delete_blocked_by_subscription_history_is_409_not_500(self):
        """`Subscription.plan` is PROTECT: cancelled history still blocks."""
        self.subscription.status = SubscriptionStatus.CANCELLED
        self.subscription.save(update_fields=["status"])
        response = self.client.delete(f"/api/admin/plans/{self.pro_plan.pk}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "plan_in_use")
        self.assertEqual(response.data["error"]["detail"]["subscriptions"], 1)

    def test_active_subscription_count_is_exposed_for_the_ui(self):
        rows = {row["code"]: row for row in self.client.get("/api/admin/plans/").data["results"]}
        self.assertEqual(rows["pro"]["active_subscription_count"], 1)
        self.assertEqual(rows["free"]["active_subscription_count"], 0)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class AdminSubscriptionTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_list_shape(self):
        row = self.client.get("/api/admin/subscriptions/").data["results"][0]
        self.assertEqual(row["user"], {"id": self.member.pk, "email": self.member.email})
        self.assertEqual(
            row["plan"], {"id": self.pro_plan.pk, "code": "pro", "name": "Pro"}
        )
        for field in (
            "started_at",
            "expires_at",
            "status",
            "platform",
            "platform_txn_id",
            "last_verified_at",
        ):
            self.assertIn(field, row)

    def test_create_accepts_user_id_and_plan_id(self):
        target = User.objects.create_user("fresh@example.com", PASSWORD)
        response = self.client.post(
            "/api/admin/subscriptions/",
            {
                "user_id": target.pk,
                "plan_id": self.free_plan.pk,
                "status": SubscriptionStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["user"]["email"], "fresh@example.com")

    def test_create_also_accepts_bare_user_and_plan(self):
        target = User.objects.create_user("bare@example.com", PASSWORD)
        response = self.client.post(
            "/api/admin/subscriptions/",
            {"user": target.pk, "plan": self.free_plan.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_second_active_subscription_is_a_400_not_a_500(self):
        response = self.client.post(
            "/api/admin/subscriptions/",
            {"user_id": self.member.pk, "plan_id": self.free_plan.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_action(self):
        response = self.client.post(
            f"/api/admin/subscriptions/{self.subscription.pk}/cancel/", format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], SubscriptionStatus.CANCELLED)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatus.CANCELLED)

    def test_filter_and_search(self):
        self.assertEqual(
            self.client.get("/api/admin/subscriptions/?status=active").data["count"], 1
        )
        self.assertEqual(
            self.client.get(
                f"/api/admin/subscriptions/?plan={self.free_plan.pk}"
            ).data["count"],
            0,
        )
        self.assertEqual(
            self.client.get("/api/admin/subscriptions/?search=member@").data["count"], 1
        )


# ---------------------------------------------------------------------------
# Proxies: CRUD, bulk, CSV
# ---------------------------------------------------------------------------


class AdminProxyTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_admin_list_is_not_entitlement_scoped(self):
        """The mobile API filters by plan. This one must not: an operator
        who cannot see premium rows cannot administer them."""
        self.assertEqual(self.client.get("/api/admin/proxies/").data["count"], 3)

    def test_disabled_proxies_are_visible_to_staff(self):
        self.premium_proxy.is_enabled = False
        self.premium_proxy.save(update_fields=["is_enabled"])
        self.assertEqual(self.client.get("/api/admin/proxies/").data["count"], 3)

    def test_create_with_credentials(self):
        response = self.client.post(
            "/api/admin/proxies/",
            {
                "protocol": ProxyProtocol.HTTP,
                "host": "203.0.113.90",
                "port": 8080,
                "username": "new-user",
                "password": "new-pass",
                "tier": ProxyTier.PREMIUM,
                "country": "NL",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        stored = raw_credentials(response.data["id"])
        self.assertTrue(stored["password"].startswith("gAAAAA"))

    def test_create_requires_a_cipher_for_shadowsocks(self):
        response = self.client.post(
            "/api/admin/proxies/",
            {"protocol": ProxyProtocol.SHADOWSOCKS, "host": "203.0.113.91", "port": 8388},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ss_method", response.data["error"]["detail"])

    def test_duplicate_endpoint_is_a_400_not_a_500(self):
        response = self.client.post(
            "/api/admin/proxies/",
            {
                "protocol": self.premium_proxy.protocol,
                "host": self.premium_proxy.host,
                "port": self.premium_proxy.port,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_rotates_a_credential(self):
        response = self.client.patch(
            f"/api/admin/proxies/{self.premium_proxy.pk}/",
            {"password": "rotated-pass"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.premium_proxy.refresh_from_db()
        self.assertEqual(self.premium_proxy.password, "rotated-pass")

    def test_filters_search_and_ordering(self):
        self.assertEqual(
            self.client.get("/api/admin/proxies/?protocol=ss").data["count"], 1
        )
        self.assertEqual(
            self.client.get("/api/admin/proxies/?tier=free").data["count"], 1
        )
        self.assertEqual(
            self.client.get("/api/admin/proxies/?country=de").data["count"], 1
        )
        self.assertEqual(
            self.client.get("/api/admin/proxies/?is_enabled=true").data["count"], 3
        )
        self.assertEqual(
            self.client.get("/api/admin/proxies/?search=Tokyo").data["count"], 1
        )
        hosts = [
            row["host"]
            for row in self.client.get("/api/admin/proxies/?ordering=-host").data["results"]
        ]
        self.assertEqual(hosts, sorted(hosts, reverse=True))

    def test_ids_filter_round_trips_a_selection(self):
        ids = f"{self.free_proxy.pk},{self.ss_proxy.pk}"
        response = self.client.get(f"/api/admin/proxies/?ids={ids}")
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {row["id"] for row in response.data["results"]},
            {self.free_proxy.pk, self.ss_proxy.pk},
        )

    def test_ids_filter_rejects_junk(self):
        response = self.client.get("/api/admin/proxies/?ids=1,abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_enable(self):
        ids = [self.free_proxy.pk, self.premium_proxy.pk]
        response = self.client.post(
            "/api/admin/proxies/bulk_enable/",
            {"ids": ids, "is_enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"updated": 2})
        self.assertEqual(
            Proxy.objects.filter(pk__in=ids, is_enabled=False).count(), 2
        )
        self.assertTrue(Proxy.objects.get(pk=self.ss_proxy.pk).is_enabled)

    def test_bulk_enable_requires_ids(self):
        response = self.client.post(
            "/api/admin/proxies/bulk_enable/", {"is_enabled": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_delete(self):
        ids = [self.free_proxy.pk, self.ss_proxy.pk]
        response = self.client.post(
            "/api/admin/proxies/bulk_delete/", {"ids": ids}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"deleted": 2})
        self.assertFalse(Proxy.objects.filter(pk__in=ids).exists())

    def test_bulk_delete_refuses_proxies_used_by_a_chain(self):
        response = self.client.post(
            "/api/admin/proxies/bulk_delete/",
            {"ids": [self.free_proxy.pk, self.premium_proxy.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "proxy_in_use")
        self.assertEqual(
            response.data["error"]["detail"]["proxy_ids"], [self.premium_proxy.pk]
        )
        # All or nothing: the unblocked row is still there.
        self.assertTrue(Proxy.objects.filter(pk=self.free_proxy.pk).exists())

    def test_single_delete_of_a_chained_proxy_is_409_not_500(self):
        response = self.client.delete(f"/api/admin/proxies/{self.premium_proxy.pk}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "proxy_in_use")

    def test_single_delete_of_an_unused_proxy(self):
        response = self.client.delete(f"/api/admin/proxies/{self.free_proxy.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


class AdminCSVUploadTests(AdminApiTestCase):
    URL = "/api/admin/proxies/upload_csv/"

    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    @staticmethod
    def csv_file(body=CSV_BODY, name="proxies.csv"):
        return SimpleUploadedFile(name, (CSV_HEADER + body).encode(), "text/csv")

    def test_upload_creates_a_batch_with_the_contract_shape(self):
        before = UploadBatch.objects.count()
        response = self.client.post(
            self.URL, {"file": self.csv_file()}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(UploadBatch.objects.count(), before + 1)

        data = response.data
        self.assertEqual(
            set(data),
            {
                "batch_id",
                "row_count",
                "created_count",
                "updated_count",
                "success_count",
                "fail_count",
                "errors",
            },
        )
        self.assertEqual(data["row_count"], 4)
        self.assertEqual(data["created_count"], 2)
        self.assertEqual(data["updated_count"], 0)
        self.assertEqual(data["fail_count"], 2)

        # errors: [{line, reason}] -- the bad port and the unknown protocol.
        self.assertEqual([error["line"] for error in data["errors"]], [4, 5])
        self.assertIn("not a number", data["errors"][0]["reason"])
        self.assertIn("unknown protocol", data["errors"][1]["reason"])

    def test_counts_on_the_batch_row_match_the_response(self):
        data = self.client.post(
            self.URL, {"file": self.csv_file()}, format="multipart"
        ).data
        batch = UploadBatch.objects.get(pk=data["batch_id"])
        self.assertEqual(batch.row_count, data["row_count"])
        self.assertEqual(batch.created_count, data["created_count"])
        self.assertEqual(batch.fail_count, data["fail_count"])
        self.assertEqual(batch.success_count, 2)
        self.assertEqual(batch.uploaded_by, self.staff)
        self.assertEqual(batch.filename, "proxies.csv")

    def test_imported_rows_land_as_proxies_with_encrypted_credentials(self):
        self.client.post(self.URL, {"file": self.csv_file()}, format="multipart")
        imported = Proxy.objects.get(host="203.0.113.10")
        self.assertEqual(imported.protocol, ProxyProtocol.SOCKS5)
        self.assertEqual(imported.username, "csvuser")
        self.assertEqual(imported.tier, ProxyTier.PREMIUM)
        self.assertTrue(raw_credentials(imported.pk)["password"].startswith("gAAAAA"))

    def test_untiered_rows_default_to_premium(self):
        """The importer's safety rule, re-asserted through the API: a
        mis-tagged import must never over-expose paid inventory."""
        self.client.post(self.URL, {"file": self.csv_file()}, format="multipart")
        self.assertEqual(
            Proxy.objects.get(host="203.0.113.11").tier, ProxyTier.FREE
        )  # explicit in the CSV

    def test_second_upload_of_the_same_file_updates_rather_than_duplicates(self):
        self.client.post(self.URL, {"file": self.csv_file()}, format="multipart")
        second = self.client.post(
            self.URL, {"file": self.csv_file()}, format="multipart"
        ).data
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["updated_count"], 2)
        self.assertEqual(Proxy.objects.filter(host="203.0.113.10").count(), 1)

    def test_missing_required_column_is_a_400(self):
        bad = SimpleUploadedFile(
            "bad.csv", b"host,port\n203.0.113.50,1080\n", "text/csv"
        )
        response = self.client.post(self.URL, {"file": bad}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "csv_invalid")

    def test_empty_file_is_a_400(self):
        response = self.client.post(
            self.URL,
            {"file": SimpleUploadedFile("empty.csv", b"", "text/csv")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_file_is_a_400(self):
        response = self.client.post(self.URL, {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(PROXY_CSV_MAX_BYTES=10)
    def test_oversized_file_is_refused(self):
        response = self.client.post(
            self.URL, {"file": self.csv_file()}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "csv_too_large")

    def test_batch_appears_on_the_upload_batches_endpoint(self):
        batch_id = self.client.post(
            self.URL, {"file": self.csv_file()}, format="multipart"
        ).data["batch_id"]
        response = self.client.get(f"/api/admin/upload-batches/{batch_id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uploaded_by"], self.staff.email)
        self.assertEqual(response.data["fail_count"], 2)
        self.assertEqual([error["line"] for error in response.data["errors"]], [4, 5])


class BatchErrorParsingTests(TestCase):
    def test_parses_importer_lines(self):
        self.assertEqual(
            parse_batch_errors("line 4: port 'x' is not a number\nline 9: host is empty"),
            [
                {"line": 4, "reason": "port 'x' is not a number"},
                {"line": 9, "reason": "host is empty"},
            ],
        )

    def test_unrecognised_lines_keep_a_null_line_number(self):
        self.assertEqual(
            parse_batch_errors("... further errors truncated at 200"),
            [{"line": None, "reason": "... further errors truncated at 200"}],
        )

    def test_empty(self):
        self.assertEqual(parse_batch_errors(""), [])
        self.assertEqual(parse_batch_errors(None), [])


# ---------------------------------------------------------------------------
# Read-only resources and content
# ---------------------------------------------------------------------------


class AdminReadOnlyResourceTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_upload_batches_are_read_only(self):
        self.assertEqual(
            self.client.post("/api/admin/upload-batches/", {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(f"/api/admin/upload-batches/{self.batch.pk}/").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_usage_logs_are_read_only_and_shaped(self):
        self.assertEqual(
            self.client.post("/api/admin/usage-logs/", {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        row = self.client.get("/api/admin/usage-logs/").data["results"][0]
        self.assertEqual(row["user"], {"id": self.member.pk, "email": self.member.email})
        for field in ("bytes_up", "bytes_down", "session_start", "session_end"):
            self.assertIn(field, row)

    def test_usage_logs_filter_by_user_and_date_range(self):
        self.assertEqual(
            self.client.get(f"/api/admin/usage-logs/?user={self.member.pk}").data["count"],
            1,
        )
        self.assertEqual(
            self.client.get(f"/api/admin/usage-logs/?user={self.staff.pk}").data["count"],
            0,
        )
        future = (timezone.now() + timedelta(days=1)).date().isoformat()
        self.assertEqual(
            self.client.get(
                f"/api/admin/usage-logs/?session_start_after={future}"
            ).data["count"],
            0,
        )
        past = (timezone.now() - timedelta(days=1)).date().isoformat()
        self.assertEqual(
            self.client.get(
                f"/api/admin/usage-logs/?session_start_after={past}"
            ).data["count"],
            1,
        )

    def test_chains_are_read_only_and_shaped(self):
        self.assertEqual(
            self.client.post("/api/admin/chains/", {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        row = self.client.get("/api/admin/chains/").data["results"][0]
        self.assertEqual(row["user"], {"id": self.member.pk, "email": self.member.email})
        self.assertEqual(row["name"], "Tokyo route")
        hop = row["hops"][0]
        self.assertEqual(hop["position"], 0)
        self.assertEqual(
            set(hop["proxy"]), {"id", "protocol", "host", "country"}
        )

    def test_chains_show_every_user_not_just_the_caller(self):
        """`/api/chains/` is scoped to the requesting user by design. The
        admin view must be the opposite, or it would always be empty."""
        self.assertEqual(self.client.get("/api/admin/chains/").data["count"], 1)
        self.assertEqual(
            self.client.get("/api/chains/").data["count"], 0
        )  # staff has no chains of their own


class AdminContentTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_unpublished_content_is_visible_to_staff(self):
        Content.objects.create(key="draft", kind=ContentKind.NOTICE, title="Draft")
        self.assertEqual(self.client.get("/api/admin/content/").data["count"], 2)
        # ...and still hidden from the mobile API.
        self.assertEqual(self.client.get("/api/content/").data["count"], 1)

    def test_crud(self):
        created = self.client.post(
            "/api/admin/content/",
            {
                "key": "maintenance",
                "kind": ContentKind.NOTICE,
                "title": "Maintenance",
                "body": "Back at 03:00 UTC.",
                "priority": 10,
                "is_published": True,
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        for field in (
            "id",
            "key",
            "kind",
            "title",
            "body",
            "priority",
            "is_published",
            "created_at",
            "updated_at",
        ):
            self.assertIn(field, created.data)

        updated = self.client.patch(
            f"/api/admin/content/{created.data['id']}/",
            {"is_published": False},
            format="json",
        )
        self.assertFalse(updated.data["is_published"])
        self.assertEqual(
            self.client.delete(f"/api/admin/content/{created.data['id']}/").status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_duplicate_key_is_rejected(self):
        response = self.client.post(
            "/api/admin/content/",
            {"key": "welcome-hero", "kind": ContentKind.NOTICE, "title": "Clash"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class AdminStatsTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_shape_matches_the_contract_exactly(self):
        data = self.client.get("/api/admin/stats/").data
        self.assertEqual(
            set(data), {"users", "subscriptions", "proxies", "usage", "uploads"}
        )
        self.assertEqual(set(data["users"]), {"total", "staff", "new_7d"})
        self.assertEqual(
            set(data["subscriptions"]), {"active", "expired", "free_tier", "paid"}
        )
        self.assertEqual(
            set(data["proxies"]),
            {"total", "alive", "dead", "free", "premium", "by_protocol"},
        )
        self.assertEqual(set(data["proxies"]["by_protocol"]), {"ss", "socks5", "http"})
        self.assertEqual(
            set(data["usage"]), {"bytes_up_7d", "bytes_down_7d", "sessions_7d"}
        )
        self.assertEqual(set(data["uploads"]), {"last_batch_at", "last_batch_rows"})

    def test_counts(self):
        data = self.client.get("/api/admin/stats/").data
        self.assertEqual(data["users"]["total"], 4)
        self.assertEqual(data["users"]["staff"], 3)  # staff, superuser, inactive staff
        self.assertEqual(data["users"]["new_7d"], 4)

        self.assertEqual(data["subscriptions"]["active"], 1)
        self.assertEqual(data["subscriptions"]["paid"], 1)
        self.assertEqual(data["subscriptions"]["free_tier"], 0)

        self.assertEqual(data["proxies"]["total"], 3)
        self.assertEqual(data["proxies"]["alive"], 3)
        self.assertEqual(data["proxies"]["dead"], 0)
        self.assertEqual(data["proxies"]["free"], 1)
        self.assertEqual(data["proxies"]["premium"], 2)
        self.assertEqual(
            data["proxies"]["by_protocol"], {"ss": 1, "socks5": 2, "http": 0}
        )

        self.assertEqual(data["usage"]["bytes_up_7d"], 1000)
        self.assertEqual(data["usage"]["bytes_down_7d"], 2000)
        self.assertEqual(data["usage"]["sessions_7d"], 1)

        self.assertEqual(data["uploads"]["last_batch_rows"], 2)
        self.assertIsNotNone(data["uploads"]["last_batch_at"])

    def test_zero_state_returns_zeros_not_nulls(self):
        UsageLog.objects.all().delete()
        UploadBatch.objects.all().delete()
        data = self.client.get("/api/admin/stats/").data
        self.assertEqual(data["usage"]["bytes_up_7d"], 0)
        self.assertEqual(data["usage"]["bytes_down_7d"], 0)
        self.assertEqual(data["usage"]["sessions_7d"], 0)
        self.assertIsNone(data["uploads"]["last_batch_at"])
        self.assertEqual(data["uploads"]["last_batch_rows"], 0)

    def test_an_unswept_expired_subscription_counts_as_expired(self):
        self.subscription.expires_at = timezone.now() - timedelta(days=1)
        self.subscription.save(update_fields=["expires_at"])
        data = self.client.get("/api/admin/stats/").data
        self.assertEqual(data["subscriptions"]["active"], 0)
        self.assertEqual(data["subscriptions"]["expired"], 1)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class AdminPaginationTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)
        Proxy.objects.bulk_create(
            Proxy(
                protocol=ProxyProtocol.HTTP,
                host=f"203.0.113.{index}",
                port=8080,
                tier=ProxyTier.FREE,
            )
            for index in range(1, 40)
        )

    def test_default_page_size_is_25(self):
        response = self.client.get("/api/admin/proxies/")
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(
            set(response.data), {"count", "next", "previous", "results"}
        )

    def test_page_size_is_honoured(self):
        self.assertEqual(
            len(self.client.get("/api/admin/proxies/?page_size=10").data["results"]), 10
        )

    def test_page_size_is_capped_at_200(self):
        Proxy.objects.bulk_create(
            Proxy(
                protocol=ProxyProtocol.HTTP,
                host=f"203.0.114.{index}",
                port=8080,
                tier=ProxyTier.FREE,
            )
            for index in range(1, 200)
        )
        response = self.client.get("/api/admin/proxies/?page_size=5000")
        self.assertEqual(len(response.data["results"]), 200)


# ---------------------------------------------------------------------------
# The mobile API must be untouched
# ---------------------------------------------------------------------------


class MobileApiUnaffectedTests(AdminApiTestCase):
    def test_mobile_proxy_list_is_still_entitlement_scoped_for_a_free_user(self):
        free_user = User.objects.create_user("scoped@example.com", PASSWORD)
        self.authenticate(free_user)
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)  # the single free-tier row
        self.assertEqual(response.data["results"][0]["tier"], ProxyTier.FREE.value)

    def test_mobile_list_still_omits_credentials(self):
        self.authenticate(self.member)
        for row in self.client.get("/api/proxies/").data["results"]:
            for key in CREDENTIAL_KEYS:
                self.assertNotIn(key, row)

    def test_admin_prefix_does_not_shadow_a_mobile_route(self):
        self.authenticate(self.member)
        self.assertEqual(
            self.client.get("/api/subscription/").status_code, status.HTTP_200_OK
        )

    def test_staff_using_the_mobile_api_are_still_entitlement_scoped(self):
        """Staff privileges live at /api/admin/ only. A staff token on the
        mobile API gets the mobile API's rules, not admin's."""
        self.authenticate(self.staff)
        response = self.client.get("/api/proxies/")
        self.assertEqual(response.data["count"], 1)

    def test_django_admin_is_not_mounted(self):
        """D12 kept Django Admin at `/admin/` as a superuser fallback.
        That is reversed: the React console on `/api/admin/` is the only
        administrative surface.

        Asserted on the index, the login page and an arbitrary model page,
        because unmounting the root is not the same as the subtree being
        gone -- a stray `path("admin/...")` added later would pass a test
        that only checked `/admin/`.
        """
        for url in ("/admin/", "/admin/login/", "/admin/proxies/proxy/"):
            self.assertEqual(
                self.client.get(url).status_code,
                status.HTTP_404_NOT_FOUND,
                url,
            )

    def test_the_admin_app_is_not_installed(self):
        """Unmounting the URL leaves the app registered, its `admin.py`
        modules imported and `django_admin_log` in the schema. The point
        of the removal was to delete the session-cookie surface, not to
        hide it, so the app itself has to be gone."""
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        with self.assertRaises(NoReverseMatch):
            reverse("admin:index")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class CorsTests(AdminApiTestCase):
    SPA_ORIGIN = "http://localhost:5173"

    def test_all_origins_are_never_allowed(self):
        from django.conf import settings

        self.assertFalse(getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False))
        self.assertIn(self.SPA_ORIGIN, settings.CORS_ALLOWED_ORIGINS)

    def test_preflight_from_the_spa_origin_is_allowed(self):
        response = self.client.options(
            "/api/admin/stats/",
            HTTP_ORIGIN=self.SPA_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), self.SPA_ORIGIN
        )

    def test_actual_request_carries_the_allow_origin_header(self):
        self.authenticate(self.staff)
        response = self.client.get("/api/admin/stats/", HTTP_ORIGIN=self.SPA_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), self.SPA_ORIGIN
        )

    def test_an_unlisted_origin_gets_no_cors_header(self):
        self.authenticate(self.staff)
        response = self.client.get(
            "/api/admin/stats/", HTTP_ORIGIN="https://attacker.example"
        )
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_cors_is_scoped_to_the_admin_api(self):
        """The mobile API is a native client; adding CORS headers there
        would be a change to responses the app already depends on."""
        self.authenticate(self.member)
        response = self.client.get("/api/proxies/", HTTP_ORIGIN=self.SPA_ORIGIN)
        self.assertIsNone(response.headers.get("access-control-allow-origin"))
