"""P17 -- mobile sign-out that actually ends the session.

Before this, sign-out was local only: the client dropped its tokens and
the refresh token stayed valid for up to 30 days. A phone that was sold,
stolen or lent kept a renewable session for a month after its owner
believed they had signed out.

Two properties are being tested, and they pull against each other:

* **It must work.** The refresh token is blacklisted and can no longer
  renew a session. That is the whole point.
* **It must not fail.** Every malformed, stale, already-revoked or
  missing body still returns 204, because a client that gets an error
  from sign-out logs it and moves on -- leaving alive exactly the token
  this endpoint exists to kill. An endpoint that is right 99% of the time
  and errors the rest is worse here than one that is quietly forgiving.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from adminapi.models import AdminAuditLog, AuditAction

User = get_user_model()

LOGOUT_URL = "/api/auth/logout/"
REFRESH_URL = "/api/auth/refresh/"


class LogoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("mobile@example.com", "Str0ngPassphrase!")
        self.refresh = RefreshToken.for_user(self.user)
        self.client.force_authenticate(self.user)

    def logout(self, body=None):
        return self.client.post(LOGOUT_URL, body if body is not None else {}, format="json")

    # -- it works -----------------------------------------------------------

    def test_logout_blacklists_the_submitted_refresh_token(self):
        response = self.logout({"refresh": str(self.refresh)})
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        jti = self.refresh["jti"]
        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=jti).exists(),
            "the refresh token was not blacklisted -- sign-out is still local only",
        )

    def test_a_blacklisted_token_can_no_longer_refresh(self):
        """The property that actually matters. Blacklisting is only
        meaningful if the refresh endpoint honours it."""
        token = str(self.refresh)

        before = self.client.post(REFRESH_URL, {"refresh": token}, format="json")
        self.assertEqual(before.status_code, status.HTTP_200_OK)

        # Rotation handed back a new refresh token; sign out with that one,
        # which is what a real client holds at this point.
        rotated = before.data["refresh"]
        self.assertEqual(
            self.logout({"refresh": rotated}).status_code,
            status.HTTP_204_NO_CONTENT,
        )

        after = self.client.post(REFRESH_URL, {"refresh": rotated}, format="json")
        self.assertEqual(
            after.status_code,
            status.HTTP_401_UNAUTHORIZED,
            "a signed-out refresh token still renews a session",
        )

    def test_the_stolen_phone_case_end_to_end(self):
        """Sign in, sign out, and confirm the 30-day refresh token is dead
        -- stated as the scenario rather than as the mechanism."""
        User.objects.filter(pk=self.user.pk).update(is_active=True)
        self.client.force_authenticate(None)
        login = self.client.post(
            "/api/auth/login/",
            {"email": "mobile@example.com", "password": "Str0ngPassphrase!"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        refresh = login.data["refresh"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertEqual(
            self.client.post(LOGOUT_URL, {"refresh": refresh}, format="json").status_code,
            status.HTTP_204_NO_CONTENT,
        )

        self.client.credentials()
        self.assertEqual(
            self.client.post(REFRESH_URL, {"refresh": refresh}, format="json").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    # -- it does not fail ---------------------------------------------------

    def test_logging_out_twice_succeeds(self):
        token = str(self.refresh)
        self.assertEqual(self.logout({"refresh": token}).status_code, 204)
        self.assertEqual(
            self.logout({"refresh": token}).status_code,
            status.HTTP_204_NO_CONTENT,
            "a repeated sign-out must be idempotent",
        )
        # And still exactly one blacklist row, not a duplicate-key error.
        self.assertEqual(
            BlacklistedToken.objects.filter(token__jti=self.refresh["jti"]).count(), 1
        )

    def test_garbage_token_succeeds(self):
        for junk in ("garbage", "a.b.c", "x" * 400, "eyJhbGciOiJIUzI1NiJ9.e30.zzz"):
            with self.subTest(junk=junk[:16]):
                self.assertEqual(
                    self.logout({"refresh": junk}).status_code,
                    status.HTTP_204_NO_CONTENT,
                )

    def test_missing_and_blank_bodies_succeed(self):
        """More forgiving than the admin endpoint, deliberately: there, a
        missing `refresh` is a 400. Here the cost of a 400 is a live
        token."""
        for body in ({}, {"refresh": ""}, {"refresh": None}, {"other": "x"}):
            with self.subTest(body=body):
                self.assertEqual(
                    self.logout(body).status_code, status.HTTP_204_NO_CONTENT
                )

    def test_an_access_token_sent_in_the_refresh_field_succeeds(self):
        """A plausible client bug. It cannot be blacklisted -- it is not a
        refresh token -- and it must not produce an error either."""
        access = str(self.refresh.access_token)
        self.assertEqual(
            self.logout({"refresh": access}).status_code, status.HTTP_204_NO_CONTENT
        )
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_an_expired_refresh_token_succeeds(self):
        import datetime

        self.refresh.set_exp(from_time=datetime.datetime(2020, 1, 1), lifetime=datetime.timedelta(seconds=1))
        self.assertEqual(
            self.logout({"refresh": str(self.refresh)}).status_code,
            status.HTTP_204_NO_CONTENT,
        )

    # -- access control and bookkeeping -------------------------------------

    def test_logout_requires_authentication(self):
        self.client.force_authenticate(None)
        response = self.client.post(
            LOGOUT_URL, {"refresh": str(self.refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_the_error_envelope_survives(self):
        """The one failing path still has to look like every other error
        on this API."""
        self.client.force_authenticate(None)
        response = self.client.post(LOGOUT_URL, {}, format="json")
        self.assertIn("error", response.data)
        self.assertEqual(response.data["error"]["status"], 401)

    def test_logout_is_audited(self):
        self.logout({"refresh": str(self.refresh)})
        entry = AdminAuditLog.objects.filter(action=AuditAction.LOGOUT).first()
        self.assertIsNotNone(entry, "no audit row was written for sign-out")
        self.assertEqual(entry.actor_id, self.user.pk)
        self.assertEqual(entry.actor_email, self.user.email)
        self.assertEqual(entry.target_type, "user")
        self.assertEqual(entry.target_id, str(self.user.pk))
        self.assertEqual(entry.payload["outcome"], "blacklisted")
        self.assertEqual(entry.payload["via"], "mobile")

    def test_a_failed_logout_is_audited_too(self):
        """A sign-out that could not revoke anything is the interesting
        one to investigate, so it must leave a row saying so rather than
        looking like a success."""
        self.logout({"refresh": "garbage"})
        entry = AdminAuditLog.objects.filter(action=AuditAction.LOGOUT).first()
        self.assertEqual(entry.payload["outcome"], "token_unusable")

    def test_the_audit_row_never_contains_the_token(self):
        token = str(self.refresh)
        self.logout({"refresh": token})
        entry = AdminAuditLog.objects.filter(action=AuditAction.LOGOUT).first()
        blob = str(entry.payload) + str(entry.changes)
        self.assertNotIn(token, blob)
        self.assertNotIn(token[:40], blob)

    def test_logout_mirrors_the_admin_endpoint_shape(self):
        """Both surfaces take `{refresh}` at `auth/logout/` and answer
        204, so a client library can share one call."""
        from django.urls import reverse

        self.assertEqual(reverse("api:auth-logout"), LOGOUT_URL)
        self.assertEqual(reverse("adminapi:auth-logout"), "/api/admin/auth/logout/")

    def test_logout_carries_a_loose_throttle_scope(self):
        from django.conf import settings

        from api.views import LogoutView

        self.assertEqual(LogoutView.throttle_scope, "logout")
        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["logout"]
        count, _, period = rate.partition("/")
        self.assertGreaterEqual(
            int(count),
            30,
            "sign-out is throttled tightly enough to fail -- that leaves "
            "live refresh tokens behind",
        )

    def test_signing_out_does_not_disturb_other_sessions(self):
        """One device signing out must not log the user out everywhere.
        Blacklisting is per token, and `OutstandingToken` rows for the
        other devices stay usable."""
        other = RefreshToken.for_user(self.user)
        self.logout({"refresh": str(self.refresh)})

        response = self.client.post(REFRESH_URL, {"refresh": str(other)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            # two minted here, plus the one rotation just issued
            3,
        )
