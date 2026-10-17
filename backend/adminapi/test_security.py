"""Security regression tests for the admin API.

Separate from `tests.py`, which covers the contract. These cover the threat
model's ranked findings, and each one is named for the thing it would catch
coming back. The ordering mirrors the severity ranking: credential
destruction and the privilege boundary between staff accounts first, since
those are the two that cannot be undone.

The attacker these are written against is not anonymous. Anonymous is
handled by `StaffGateTests`. These assume a *valid staff credential in the
wrong hands* -- phished, reused, or a departing contractor -- against which
a permission class is worth nothing, and every control that still works is
a limit, a log, or a boundary between staff.
"""

import inspect
import json
import logging
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from adminapi.filters import MAX_IDS
from adminapi.tests import (
    CSV_HEADER,
    NEW_PASSWORD,
    PASSWORD,
    AdminApiTestCase,
    User,
)
from billing.models import Plan
from proxies.models import Proxy, ProxyProtocol, ProxyTier, UploadBatch


# ---------------------------------------------------------------------------
# Privilege boundaries between staff (T2)
# ---------------------------------------------------------------------------


class PrivilegeEscalationTests(AdminApiTestCase):
    """The contract grants a flat `is_staff` role with no internal
    boundary. Without these, staff *is* superuser: one PATCH mints further
    staff, resets any password and rewrites the entitlement model. The
    original argument ran through Django Admin at `/admin/`, a broader
    write surface still; removing that console removed the illustration,
    not the escalation."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_staff_cannot_promote_themselves(self):
        response = self.client.patch(
            f"/api/admin/users/{self.staff.pk}/",
            {"is_superuser": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "privilege_change_forbidden")
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_superuser)

    def test_staff_cannot_promote_another_account(self):
        response = self.client.patch(
            f"/api/admin/users/{self.member.pk}/",
            {"is_superuser": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_superuser)

    def test_staff_cannot_mint_staff(self):
        response = self.client.patch(
            f"/api/admin/users/{self.member.pk}/", {"is_staff": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)

    def test_staff_cannot_create_a_privileged_account(self):
        response = self.client.post(
            "/api/admin/users/",
            {
                "email": "backdoor@example.com",
                "password": NEW_PASSWORD,
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(email="backdoor@example.com").exists())

    def test_staff_cannot_deactivate_a_superuser(self):
        """Not escalation, but it removes the only account that could stop
        an abusive staff member."""
        response = self.client.patch(
            f"/api/admin/users/{self.superuser.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_active)

    def test_an_unchanged_round_trip_still_works_for_staff(self):
        """The flags are read-only for staff, not forbidden to echo. A SPA
        that PUTs the whole object back must not be rejected for sending
        the values it was just handed."""
        current = self.client.get(f"/api/admin/users/{self.member.pk}/").data
        response = self.client.patch(
            f"/api/admin/users/{self.member.pk}/",
            {
                "is_staff": current["is_staff"],
                "is_superuser": current["is_superuser"],
                "first_name": "Renamed",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["first_name"], "Renamed")

    def test_superuser_can_change_another_accounts_flags(self):
        self.authenticate(self.superuser)
        response = self.client.patch(
            f"/api/admin/users/{self.member.pk}/", {"is_staff": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_staff)

    def test_even_a_superuser_cannot_change_their_own_flags(self):
        """Closes self-escalation and, incidentally, the last-superuser
        lockout: the change has to come from a second account."""
        self.authenticate(self.superuser)
        response = self.client.patch(
            f"/api/admin/users/{self.superuser.pk}/",
            {"is_superuser": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_superuser)

    def test_privilege_change_is_audited_with_before_and_after(self):
        self.authenticate(self.superuser)
        with self.assertLogs("proxycrack.audit", level="WARNING") as captured:
            self.client.patch(
                f"/api/admin/users/{self.member.pk}/",
                {"is_staff": True},
                format="json",
            )
        blob = "\n".join(captured.output)
        self.assertIn("admin_user_privileges_changed", blob)
        self.assertIn('"from": false', blob)
        self.assertIn('"to": true', blob)

    def test_groups_and_permissions_are_not_exposed(self):
        """A quieter escalation path than `is_superuser`, and the reason the
        serializer's field list is explicit rather than `__all__`."""
        body = self.client.get(f"/api/admin/users/{self.member.pk}/").content.decode()
        self.assertNotIn("groups", body)
        self.assertNotIn("user_permissions", body)

    def test_password_is_not_writable_through_a_user_update(self):
        response = self.client.patch(
            f"/api/admin/users/{self.member.pk}/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.member.refresh_from_db()
        self.assertFalse(self.member.check_password(NEW_PASSWORD))


class SetPasswordBoundaryTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.peer = User.objects.create_user(
            "peer@example.com", PASSWORD, is_staff=True
        )
        self.authenticate(self.staff)

    def test_staff_cannot_set_the_superusers_password(self):
        """The quietest route to the worst outcome: own the superuser,
        then sign in as them. With Django Admin removed this boundary is
        the whole of the defence rather than its first half."""
        response = self.client.post(
            f"/api/admin/users/{self.superuser.pk}/set_password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "set_password_forbidden")
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.check_password(PASSWORD))

    def test_staff_cannot_set_a_peer_staff_password(self):
        response = self.client.post(
            f"/api/admin/users/{self.peer.pk}/set_password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.peer.refresh_from_db()
        self.assertTrue(self.peer.check_password(PASSWORD))

    def test_staff_can_reset_an_ordinary_user(self):
        response = self.client.post(
            f"/api/admin/users/{self.member.pk}/set_password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(NEW_PASSWORD))

    def test_staff_can_always_set_their_own(self):
        response = self.client.post(
            f"/api/admin/users/{self.staff.pk}/set_password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_superuser_may_reset_anyone(self):
        self.authenticate(self.superuser)
        for target in (self.staff, self.peer, self.member):
            response = self.client.post(
                f"/api/admin/users/{target.pk}/set_password/",
                {"password": NEW_PASSWORD},
                format="json",
            )
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, target.email
            )

    def test_reset_revokes_the_targets_refresh_tokens(self):
        """A reset that leaves the old sessions renewable is not a reset."""
        victim_refresh = self.refresh_token(self.member)
        self.client.post(
            f"/api/admin/users/{self.member.pk}/set_password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        response = APIClient().post(
            "/api/admin/auth/refresh/", {"refresh": victim_refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_weak_passwords_are_rejected(self):
        """The admin path must not be the weak one: the mobile register
        endpoint runs AUTH_PASSWORD_VALIDATORS, so this does too."""
        response = self.client.post(
            f"/api/admin/users/{self.member.pk}/set_password/",
            {"password": "short"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_is_audited_without_the_password(self):
        with self.assertLogs("proxycrack.audit", level="WARNING") as captured:
            self.client.post(
                f"/api/admin/users/{self.member.pk}/set_password/",
                {"password": NEW_PASSWORD},
                format="json",
            )
        blob = "\n".join(captured.output)
        self.assertIn("admin_set_password", blob)
        self.assertIn(str(self.member.pk), blob)
        self.assertNotIn(NEW_PASSWORD, blob)


# ---------------------------------------------------------------------------
# The entitlement flip (T3) -- the cross-surface attack
# ---------------------------------------------------------------------------


class EntitlementFlipTests(AdminApiTestCase):
    """A staff account that wants the credentials need not touch the
    credential endpoint. One PATCH re-entitles every free account, and the
    inventory is then scraped through the *mobile* API with throwaway
    accounts: no admin throttle touched, no credential audit line written.
    Every control guarding this API is blind to it, so the plan write
    itself has to be the loud event."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_flipping_the_free_plan_tier_is_audited_at_warning(self):
        with self.assertLogs("proxycrack.audit", level="WARNING") as captured:
            response = self.client.patch(
                f"/api/admin/plans/{self.free_plan.pk}/",
                {"max_proxy_tier": ProxyTier.PREMIUM},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        blob = "\n".join(captured.output)
        self.assertIn("admin_plan_entitlement_changed", blob)
        self.assertIn('"max_proxy_tier"', blob)
        self.assertIn('"from": "free"', blob)
        self.assertIn('"to": "premium"', blob)
        self.assertIn(self.staff.email, blob)

    def test_the_flip_really_does_reach_the_mobile_api(self):
        """Documents the blast radius rather than preventing it: re-tiering
        a plan is a legitimate operator action, so the control is the audit
        record, not a refusal."""
        free_user = User.objects.create_user("flip@example.com", PASSWORD)
        mobile = APIClient()
        mobile.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.mobile_access_token(free_user)}"
        )
        self.assertEqual(mobile.get("/api/proxies/").data["count"], 1)

        self.client.patch(
            f"/api/admin/plans/{self.free_plan.pk}/",
            {"max_proxy_tier": ProxyTier.PREMIUM},
            format="json",
        )
        self.assertEqual(mobile.get("/api/proxies/").data["count"], 3)

    def test_every_entitlement_field_is_audited(self):
        for field, value in (
            ("max_chain_length", 9),
            ("bandwidth_limit_bytes", 99999),
            ("max_devices", 7),
            ("is_free_tier", True),
        ):
            with self.assertLogs("proxycrack.audit", level="WARNING") as captured:
                response = self.client.patch(
                    f"/api/admin/plans/{self.pro_plan.pk}/",
                    {field: value},
                    format="json",
                )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn(f'"{field}"', "\n".join(captured.output))

    def test_a_cosmetic_edit_is_not_logged_as_an_entitlement_change(self):
        """An alert that fires on every edit is one nobody reads."""
        with self.assertLogs("proxycrack.audit", level="INFO") as captured:
            self.client.patch(
                f"/api/admin/plans/{self.pro_plan.pk}/",
                {"name": "Pro (renamed)"},
                format="json",
            )
            logging.getLogger("proxycrack.audit").info("marker")
        self.assertNotIn("admin_plan_entitlement_changed", "\n".join(captured.output))

    def test_the_last_active_free_plan_cannot_be_deactivated(self):
        """Without one, `resolve_entitlement` silently drops every free user
        to the hardcoded ENTITLEMENT_FALLBACK -- a product-wide change with
        no error anywhere."""
        response = self.client.patch(
            f"/api/admin/plans/{self.free_plan.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["detail"]["code"], "last_free_plan")
        self.free_plan.refresh_from_db()
        self.assertTrue(self.free_plan.is_active)

    def test_a_free_plan_can_be_retired_once_a_replacement_exists(self):
        Plan.objects.create(
            code="free-v2",
            name="Free v2",
            is_free_tier=True,
            is_active=True,
            max_proxy_tier=ProxyTier.FREE,
        )
        response = self.client.patch(
            f"/api/admin/plans/{self.free_plan.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)


# ---------------------------------------------------------------------------
# CSV import: the credential-blanking regression (T8 / G-06)
# ---------------------------------------------------------------------------


class CSVCredentialPreservationTests(AdminApiTestCase):
    """G-06. An operator refreshing country and latency must not destroy
    the credentials. They are Fernet-encrypted with no plaintext copy
    anywhere, so the loss was irrecoverable -- and the response said
    "success"."""

    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)
        self.live = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="203.0.113.200",
            port=1080,
            tier=ProxyTier.PREMIUM,
            username="live_user",
            password="live_pass",
            country="US",
            city="Reno",
        )

    def upload(self, text, name="refresh.csv"):
        return self.client.post(
            "/api/admin/proxies/upload_csv/",
            {"file": SimpleUploadedFile(name, text.encode(), "text/csv")},
            format="multipart",
        )

    def test_absent_credential_columns_leave_credentials_intact(self):
        response = self.upload(
            "protocol,host,port,country,city\nsocks5,203.0.113.200,1080,CA,Toronto\n"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["updated_count"], 1)

        self.live.refresh_from_db()
        self.assertEqual(self.live.username, "live_user")
        self.assertEqual(self.live.password, "live_pass")
        # ...while the columns that *were* present did update.
        self.assertEqual(self.live.country, "CA")
        self.assertEqual(self.live.city, "Toronto")

    def test_a_minimal_csv_leaves_every_optional_field_intact(self):
        self.upload("protocol,host,port\nsocks5,203.0.113.200,1080\n")
        self.live.refresh_from_db()
        self.assertEqual(self.live.username, "live_user")
        self.assertEqual(self.live.password, "live_pass")
        self.assertEqual(self.live.country, "US")
        self.assertEqual(self.live.city, "Reno")
        self.assertEqual(self.live.tier, ProxyTier.PREMIUM)

    def test_a_present_but_empty_column_is_a_deliberate_clear(self):
        """Absent means "leave alone"; present-and-empty means "blank it".
        That distinction is the entire fix, so both halves are asserted --
        otherwise the obvious over-correction (never write an empty value)
        would pass the test above and quietly break clearing."""
        self.upload(
            "protocol,host,port,username,password\nsocks5,203.0.113.200,1080,,\n"
        )
        self.live.refresh_from_db()
        self.assertEqual(self.live.username, "")
        self.assertEqual(self.live.password, "")

    def test_an_absent_tier_column_does_not_re_tier_an_existing_proxy(self):
        free = Proxy.objects.create(
            protocol=ProxyProtocol.HTTP,
            host="203.0.113.201",
            port=8080,
            tier=ProxyTier.FREE,
        )
        self.upload("protocol,host,port\nhttp,203.0.113.201,8080\n")
        free.refresh_from_db()
        self.assertEqual(free.tier, ProxyTier.FREE)

    def test_a_new_row_without_a_tier_column_still_fails_safe_to_premium(self):
        self.upload("protocol,host,port\nhttp,203.0.113.202,8080\n")
        self.assertEqual(
            Proxy.objects.get(host="203.0.113.202").tier, ProxyTier.PREMIUM
        )

    def test_an_absent_cipher_column_preserves_an_existing_shadowsocks_cipher(self):
        self.upload("protocol,host,port,country\nss,198.51.100.3,8388,JP\n")
        self.ss_proxy.refresh_from_db()
        self.assertEqual(self.ss_proxy.ss_method, "aes-256-gcm")

    def test_a_new_shadowsocks_row_still_requires_a_cipher(self):
        response = self.upload("protocol,host,port\nss,203.0.113.210,8388\n")
        self.assertEqual(response.data["fail_count"], 1)
        self.assertIn("ss_method", response.data["errors"][0]["reason"])
        self.assertFalse(Proxy.objects.filter(host="203.0.113.210").exists())


class CSVLimitTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_a_non_csv_extension_is_refused_on_the_api_path(self):
        """The extension check used to live in `proxies/forms.py`, a
        Django admin form this endpoint never touched -- so it was
        re-implemented here. That form is gone with the console and this
        is now the only check of its kind in the project."""
        response = self.client.post(
            "/api/admin/proxies/upload_csv/",
            {"file": SimpleUploadedFile("payload.png", b"\x89PNG\r\n", "image/png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "csv_invalid")

    def test_a_traversal_shaped_filename_is_stored_as_a_basename(self):
        self.client.post(
            "/api/admin/proxies/upload_csv/",
            {
                "file": SimpleUploadedFile(
                    "../../../../etc/passwd.csv",
                    (CSV_HEADER + "socks5,203.0.113.77,1080,,,,US,,free\n").encode(),
                    "text/csv",
                )
            },
            format="multipart",
        )
        filename = UploadBatch.objects.order_by("-created_at").first().filename
        self.assertNotIn("/", filename)
        self.assertNotIn("\\", filename)

    def test_non_utf8_bytes_are_a_400_not_a_500(self):
        response = self.client.post(
            "/api/admin/proxies/upload_csv/",
            {
                "file": SimpleUploadedFile(
                    "latin.csv", b"protocol,host,port\n\xff\xfe\n", "text/csv"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(PROXY_CSV_MAX_ROWS=2)
    def test_the_row_cap_stops_the_import_and_reports_it(self):
        rows = "".join(
            f"socks5,203.0.113.{index},1080,,,,US,,free\n" for index in range(1, 6)
        )
        response = self.client.post(
            "/api/admin/proxies/upload_csv/",
            {
                "file": SimpleUploadedFile(
                    "many.csv", (CSV_HEADER + rows).encode(), "text/csv"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.data["row_count"], 2)
        self.assertTrue(
            any("row limit" in error["reason"] for error in response.data["errors"])
        )

    def test_a_database_rejection_does_not_echo_row_values(self):
        """`UploadBatch.errors` is rendered in the SPA and may be exported.
        One of the columns in a rejected row is a password."""
        row = (
            "socks5,203.0.113.99,1080,u,sup3rs3cret-password,,US,,free\n"
        )
        with mock.patch(
            "proxies.importers.Proxy.objects.update_or_create",
            side_effect=ValueError("duplicate key: 'sup3rs3cret-password'"),
        ):
            response = self.client.post(
                "/api/admin/proxies/upload_csv/",
                {
                    "file": SimpleUploadedFile(
                        "boom.csv", (CSV_HEADER + row).encode(), "text/csv"
                    )
                },
                format="multipart",
            )
        self.assertEqual(response.data["fail_count"], 1)
        self.assertNotIn("sup3rs3cret-password", json.dumps(response.data))
        batch = UploadBatch.objects.get(pk=response.data["batch_id"])
        self.assertNotIn("sup3rs3cret-password", batch.errors)


# ---------------------------------------------------------------------------
# Limits: bulk bodies, renderers, ordering, throttle scopes
# ---------------------------------------------------------------------------


class BulkLimitTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_bulk_delete_caps_the_id_list(self):
        response = self.client.post(
            "/api/admin/proxies/bulk_delete/",
            {"ids": list(range(1, MAX_IDS + 2))},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Proxy.objects.count(), 3)

    def test_bulk_enable_caps_the_id_list(self):
        response = self.client.post(
            "/api/admin/proxies/bulk_enable/",
            {"ids": list(range(1, MAX_IDS + 2)), "is_enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_enable_rejects_a_non_integer_id(self):
        response = self.client.post(
            "/api/admin/proxies/bulk_enable/",
            {"ids": [self.free_proxy.pk, "not-an-id"], "is_enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_operations_are_audited_with_the_id_list(self):
        with self.assertLogs("proxycrack.audit", level="WARNING") as captured:
            self.client.post(
                "/api/admin/proxies/bulk_enable/",
                {"ids": [self.free_proxy.pk], "is_enabled": False},
                format="json",
            )
        blob = "\n".join(captured.output)
        self.assertIn("admin_bulk_enable", blob)
        self.assertIn(str(self.free_proxy.pk), blob)


class RendererAndQueryShapeTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.staff)

    def test_no_query_parameter_widens_the_proxy_list(self):
        """One line of code turns a long scrape into a single request, so
        the guard is structural: there is no parameter the list serializer
        reads at all."""
        for query in (
            "?expand=credentials",
            "?fields=username,password",
            "?include=username",
            "?format=json",
        ):
            body = self.client.get(f"/api/admin/proxies/{query}").content.decode()
            for secret in ("paid-pass", "paid-user", "aes-256-gcm"):
                self.assertNotIn(secret, body, query)

    def test_an_unregistered_renderer_is_not_a_second_serialisation_path(self):
        response = self.client.get("/api/admin/proxies/?format=csv")
        self.assertIn(
            response.status_code,
            (status.HTTP_404_NOT_FOUND, status.HTTP_406_NOT_ACCEPTABLE),
        )
        self.assertNotIn("paid-pass", response.content.decode(errors="ignore"))

    def test_ordering_fields_do_not_traverse_into_the_user_model(self):
        """`?ordering=user__password` would be a slow but real oracle over
        password hashes."""
        from adminapi import views

        for view_class in (
            views.AdminUserViewSet,
            views.AdminProxyViewSet,
            views.AdminSubscriptionViewSet,
            views.AdminUsageLogViewSet,
            views.AdminChainViewSet,
            views.AdminContentViewSet,
            views.AdminPlanViewSet,
            views.AdminUploadBatchViewSet,
        ):
            fields = view_class.ordering_fields
            self.assertNotEqual(fields, "__all__", view_class.__name__)
            for field in fields:
                self.assertNotIn("__", field, f"{view_class.__name__}.{field}")

    def test_no_admin_serializer_uses_all_fields(self):
        """`__all__` would publish every future model field automatically --
        on `User` that means the password hash, `groups` and
        `user_permissions`."""
        from adminapi import serializers as admin_serializers

        for name, obj in inspect.getmembers(admin_serializers, inspect.isclass):
            meta = getattr(obj, "Meta", None)
            if meta is None:
                continue
            self.assertNotEqual(getattr(meta, "fields", None), "__all__", name)
            self.assertFalse(hasattr(meta, "exclude"), name)


class ThrottleScopeTests(AdminApiTestCase):
    """The numbers are configuration; that the right scope is attached to
    the right endpoint is code, and that is what is asserted here."""

    def test_credential_reveal_carries_both_credential_throttles(self):
        from adminapi.throttling import (
            AdminCredentialDailyThrottle,
            AdminCredentialHourlyThrottle,
        )
        from adminapi.views import AdminProxyViewSet

        view = AdminProxyViewSet()
        view.action = "reveal"
        view.request = None
        classes = {type(throttle) for throttle in view.get_throttles()}
        self.assertEqual(
            classes, {AdminCredentialHourlyThrottle, AdminCredentialDailyThrottle}
        )

    def test_every_admin_scope_has_a_configured_rate(self):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in (
            "admin",
            "admin_login",
            "admin_login_email",
            "admin_refresh",
            "admin_proxy_credentials",
            "admin_proxy_credentials_day",
            "admin_write",
            "admin_bulk",
            "admin_csv_upload",
        ):
            self.assertIn(scope, rates)
            self.assertTrue(rates[scope], scope)

    def test_the_credential_budget_actually_runs_out(self):
        """`override_settings` cannot reach a throttle rate: DRF binds
        `SimpleRateThrottle.THROTTLE_RATES` to the settings dict at import
        time, so changing REST_FRAMEWORK later rebinds `api_settings` and
        leaves the class attribute pointing at the original dict. The class
        attribute is therefore patched directly -- `__init__` honours a
        pre-set `rate`.
        """
        from adminapi.throttling import AdminCredentialHourlyThrottle

        self.authenticate(self.staff)
        with mock.patch.object(
            AdminCredentialHourlyThrottle, "rate", "3/hour", create=True
        ):
            codes = [
                self.client.post(
                    f"/api/admin/proxies/{self.premium_proxy.pk}/reveal/"
                ).status_code
                for _ in range(5)
            ]
        self.assertEqual(codes[:3], [status.HTTP_200_OK] * 3)
        self.assertEqual(codes[3], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_the_daily_credential_ceiling_is_enforced_independently(self):
        """The hourly limit bounds a burst; this one bounds patience, which
        is what a scraper actually has."""
        from adminapi.throttling import AdminCredentialDailyThrottle

        self.authenticate(self.staff)
        with mock.patch.object(
            AdminCredentialDailyThrottle, "rate", "2/day", create=True
        ):
            codes = [
                self.client.post(
                    f"/api/admin/proxies/{self.premium_proxy.pk}/reveal/"
                ).status_code
                for _ in range(4)
            ]
        self.assertEqual(codes[:2], [status.HTTP_200_OK] * 2)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_only_failed_logins_consume_the_per_email_budget(self):
        """An operator signing in through a working day must never lock
        themselves out -- that is how a limit ends up raised to a number
        that stops limiting anything. A guesser produces nothing but
        failures, so a failure-only counter costs them everything."""
        from adminapi.throttling import AdminLoginEmailThrottle

        with mock.patch.object(
            AdminLoginEmailThrottle, "rate", "2/hour", create=True
        ):
            for _ in range(5):
                ok = self.client.post(
                    "/api/admin/auth/login/",
                    {"email": self.staff.email, "password": PASSWORD},
                    format="json",
                )
                self.assertEqual(ok.status_code, status.HTTP_200_OK)

            codes = [
                self.client.post(
                    "/api/admin/auth/login/",
                    {"email": self.staff.email, "password": "wrong-password-here"},
                    format="json",
                ).status_code
                for _ in range(4)
            ]
        self.assertEqual(codes[:2], [status.HTTP_403_FORBIDDEN] * 2)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_the_per_email_budget_does_not_block_a_different_operator(self):
        """Keyed per address, so one attacker cannot lock every admin out
        by burning a shared counter."""
        from adminapi.throttling import AdminLoginEmailThrottle

        with mock.patch.object(
            AdminLoginEmailThrottle, "rate", "1/hour", create=True
        ):
            for _ in range(3):
                self.client.post(
                    "/api/admin/auth/login/",
                    {"email": self.staff.email, "password": "wrong"},
                    format="json",
                )
            response = self.client.post(
                "/api/admin/auth/login/",
                {"email": self.superuser.email, "password": PASSWORD},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CacheConfigurationTests(TestCase):
    """Throttling is only as real as the cache behind it. On a per-process
    backend every limit in the product is multiplied by the worker count
    and reset on every deploy."""

    def test_caches_is_configured_explicitly(self):
        self.assertIn("default", settings.CACHES)
        self.assertTrue(settings.CACHES["default"].get("BACKEND"))

    def test_the_deploy_check_fails_on_a_per_process_cache(self):
        from core.checks import check_throttle_cache_is_shared

        with override_settings(
            CACHES={
                "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
            }
        ):
            errors = check_throttle_cache_is_shared(None)
        self.assertEqual([error.id for error in errors], ["core.E002"])

    def test_the_deploy_check_passes_on_a_shared_cache(self):
        from core.checks import check_throttle_cache_is_shared

        with override_settings(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": "redis://127.0.0.1:6379/1",
                }
            }
        ):
            self.assertEqual(check_throttle_cache_is_shared(None), [])

    def test_the_deploy_check_catches_development_secrets(self):
        from core.checks import check_production_secrets

        with override_settings(
            SECRET_KEY="dev-insecure-change-me",
            DEBUG=True,
            ALLOWED_HOSTS=["*"],
            SIMPLE_JWT={**settings.SIMPLE_JWT, "SIGNING_KEY": "dev-insecure-change-me"},
        ):
            ids = sorted(problem.id for problem in check_production_secrets(None))
        self.assertEqual(ids, ["core.E003", "core.E004", "core.E005", "core.W001"])

    def test_the_deploy_check_passes_on_a_configured_production_env(self):
        from core.checks import check_production_secrets

        with override_settings(
            SECRET_KEY="a-real-50-character-secret-key-for-production-use",
            DEBUG=False,
            ALLOWED_HOSTS=["admin.example.com"],
            SIMPLE_JWT={**settings.SIMPLE_JWT, "SIGNING_KEY": "a-different-jwt-key"},
        ):
            self.assertEqual(check_production_secrets(None), [])


class CorsHardeningTests(AdminApiTestCase):
    def test_a_null_origin_is_not_allowed(self):
        """`Origin: null` is what a sandboxed iframe and a `file://` page
        send."""
        self.authenticate(self.staff)
        response = self.client.get("/api/admin/stats/", HTTP_ORIGIN="null")
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_a_lookalike_origin_is_not_allowed(self):
        self.authenticate(self.staff)
        for origin in (
            "http://localhost:5173.evil.example",
            "http://evil-localhost:5173",
            "https://localhost:5173",
        ):
            response = self.client.get("/api/admin/stats/", HTTP_ORIGIN=origin)
            self.assertIsNone(
                response.headers.get("access-control-allow-origin"), origin
            )

    def test_credentials_are_never_allowed(self):
        """The token travels in the Authorization header, so there is
        nothing a browser would attach on its own and nothing to gain.

        The reason this matters changed when Django Admin was removed. It
        used to be containment: `/admin/` issued session cookies and
        credentialed CORS would have put them within reach of the origin
        list. Production now issues no identity-bearing cookie at all, so
        the setting holds nothing back -- but turning it on would
        reintroduce CSRF as a live concern in exchange for nothing, which
        is a worse trade than the old one."""
        self.assertFalse(settings.CORS_ALLOW_CREDENTIALS)
        self.authenticate(self.staff)
        response = self.client.get(
            "/api/admin/stats/", HTTP_ORIGIN="http://localhost:5173"
        )
        self.assertNotEqual(
            response.headers.get("access-control-allow-credentials"), "true"
        )

    def test_vary_origin_is_set(self):
        """Otherwise a shared cache can serve one origin's CORS headers to
        another."""
        self.authenticate(self.staff)
        response = self.client.get(
            "/api/admin/stats/", HTTP_ORIGIN="http://localhost:5173"
        )
        self.assertIn("origin", response.headers.get("vary", "").lower())

    def test_any_configured_origin_regex_is_anchored(self):
        for pattern in getattr(settings, "CORS_ALLOWED_ORIGIN_REGEXES", []):
            self.assertTrue(pattern.startswith("^"), pattern)
            self.assertTrue(pattern.endswith("$"), pattern)
