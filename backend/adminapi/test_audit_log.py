"""P9: the durable, append-only admin audit log.

Three things have to hold, and each is tested against the thing that
would actually defeat it rather than against the happy path:

1. **It is written.** Every event the threat model (section 6.3) names
   lands in the table, and it lands via the existing `audit()` seam --
   so `AuditedEndpointCoverageTests` drives the real endpoints rather
   than calling `audit()` directly. A test that calls the seam proves the
   seam works; only a test that calls the *view* proves the view is
   wired to it.

2. **It cannot be rewritten or erased.** Not by `save()`, not by
   `.update()`, not by `.delete()` on a queryset -- which is the one that
   matters, because a queryset write never loads an instance and sails
   past any model-level guard -- and not by raw SQL, which the database
   trigger refuses.

3. **It survives its subject.** Deleting a user must not delete the
   record of what that user did; otherwise anyone with user-delete rights
   can erase their own history.

And one rule that is not about durability at all: **no credential, no
password, no token ever enters the table.** An audit log holding the
secrets is a second copy of the asset, usually behind weaker access
control than the first.
"""

import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from adminapi.audit import SECRET_KEY_HINTS, audit, scrub, target_from
from adminapi.models import (
    HARD_FLOOR_DAYS,
    AdminAuditLog,
    AuditAction,
    AuditLogImmutable,
    delete_for_retention,
)
from billing.models import Plan, Subscription, SubscriptionStatus
from proxies.models import Proxy, ProxyProtocol, ProxyTier

User = get_user_model()


def make_entry(**kwargs):
    defaults = {
        "action": AuditAction.CREDENTIAL_ACCESS,
        "actor_email": "staff@example.com",
        "target_type": "proxy",
        "target_id": "1",
    }
    defaults.update(kwargs)
    return AdminAuditLog.objects.create(**defaults)


def aged_entry(days, **kwargs):
    """A row that is already `days` old when it is written.

    Backdating an existing row is impossible by design -- the model, the
    queryset and the database trigger all refuse an UPDATE -- so the age
    has to be set at insert time. That is exactly why `created_at` is
    `default=timezone.now` rather than `auto_now_add`: with `auto_now_add`
    the field is not writable, and the only way to produce an old row for
    a retention test would be to weaken one of the guards under test.
    """
    kwargs.setdefault("created_at", timezone.now() - timedelta(days=days))
    return make_entry(**kwargs)


# ---------------------------------------------------------------------------
# Append-only, at every layer
# ---------------------------------------------------------------------------


class AppendOnlyModelTests(TestCase):
    def test_a_row_can_be_created(self):
        entry = make_entry()
        self.assertIsNotNone(entry.pk)

    def test_saving_an_existing_row_is_refused(self):
        entry = make_entry()
        entry.action = "something_else"
        with self.assertRaises(AuditLogImmutable):
            entry.save()

    def test_the_refused_save_changed_nothing(self):
        entry = make_entry(action="credential_access")
        entry.action = "tampered"
        with self.assertRaises(AuditLogImmutable):
            entry.save()
        entry.refresh_from_db()
        self.assertEqual(entry.action, "credential_access")

    def test_deleting_an_instance_is_refused(self):
        entry = make_entry()
        with self.assertRaises(AuditLogImmutable):
            entry.delete()
        self.assertTrue(AdminAuditLog.objects.filter(pk=entry.pk).exists())

    def test_queryset_update_is_refused(self):
        """The layer that matters most: a queryset write never loads an
        instance, so a model-level `save()` guard alone would not see it."""
        make_entry()
        with self.assertRaises(AuditLogImmutable):
            AdminAuditLog.objects.all().update(action="tampered")

    def test_queryset_delete_is_refused(self):
        """`objects.filter(...).delete()` is the shortest path to
        destroying the trail and the one a reasonable person writes
        without thinking about it."""
        make_entry()
        with self.assertRaises(AuditLogImmutable):
            AdminAuditLog.objects.filter(action=AuditAction.CREDENTIAL_ACCESS).delete()
        self.assertEqual(AdminAuditLog.objects.count(), 1)

    def test_bulk_update_is_refused(self):
        entry = make_entry()
        entry.action = "tampered"
        with self.assertRaises(AuditLogImmutable):
            AdminAuditLog.objects.bulk_update([entry], ["action"])

    def test_the_error_names_the_sanctioned_path(self):
        make_entry()
        with self.assertRaises(AuditLogImmutable) as caught:
            AdminAuditLog.objects.all().delete()
        self.assertIn("prune_audit_log", str(caught.exception))

    def test_the_retention_path_can_delete(self):
        """The one sanctioned exception, and it is a separate function
        rather than a flag on `delete()` -- so `grep` finds every caller
        that can remove an audit row."""
        entry = aged_entry(400)
        deleted, _ = delete_for_retention(AdminAuditLog.objects.filter(pk=entry.pk))
        self.assertEqual(deleted, 1)
        self.assertEqual(AdminAuditLog.objects.count(), 0)


class DatabaseTriggerTests(TestCase):
    """The layer the ORM guards cannot reach: raw SQL.

    "Append-only" that a `dbshell` session defeats is a convention with
    good manners, not a control. These run statements directly against
    the connection, exactly as a reporting job with write credentials or
    a migration written next year would.
    """

    def raw(self, sql, params):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql, params)

    def test_raw_sql_update_is_refused_by_the_database(self):
        entry = make_entry()
        with self.assertRaises(Exception) as caught:
            self.raw(
                "UPDATE adminapi_adminauditlog SET action = %s WHERE id = %s",
                ["tampered", entry.pk],
            )
        self.assertIn("append-only", str(caught.exception).lower())
        entry.refresh_from_db()
        self.assertEqual(entry.action, AuditAction.CREDENTIAL_ACCESS)

    def test_raw_sql_cannot_delete_a_recent_row(self):
        """The part of DELETE that is never legitimate: erasing the
        recent past. Retention is allowed to remove old rows and the
        trigger lets it; nothing is allowed to remove last week."""
        entry = make_entry()
        with self.assertRaises(Exception) as caught:
            self.raw("DELETE FROM adminapi_adminauditlog WHERE id = %s", [entry.pk])
        self.assertIn("refusing to delete", str(caught.exception).lower())
        self.assertTrue(AdminAuditLog.objects.filter(pk=entry.pk).exists())

    def test_the_trigger_allows_a_delete_past_the_hard_floor(self):
        """Otherwise retention could never run at all."""
        entry = aged_entry(HARD_FLOOR_DAYS + 5)
        self.raw("DELETE FROM adminapi_adminauditlog WHERE id = %s", [entry.pk])
        self.assertFalse(AdminAuditLog.objects.filter(pk=entry.pk).exists())

    def test_the_only_permitted_update_is_unsetting_the_actor(self):
        """The one carve-out, and it is the database's own `SET_NULL`.

        Without it, deleting any user who had ever used the console would
        fail, and the obvious "fix" would be to weaken the FK to CASCADE
        and erase their history with them -- the exact outcome this table
        exists to prevent.
        """
        actor = User.objects.create_user("a@example.com", "Str0ngPass!phrase")
        entry = make_entry(actor=actor, actor_email=actor.email)
        self.raw(
            "UPDATE adminapi_adminauditlog SET actor_id = NULL WHERE id = %s",
            [entry.pk],
        )
        entry.refresh_from_db()
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.actor_email, "a@example.com")

    def test_the_carve_out_does_not_allow_editing_anything_else(self):
        """Nulling the actor *and* rewriting the action in one statement
        would otherwise slip through the exception."""
        actor = User.objects.create_user("b@example.com", "Str0ngPass!phrase")
        entry = make_entry(actor=actor, actor_email=actor.email)
        with self.assertRaises(Exception) as caught:
            self.raw(
                "UPDATE adminapi_adminauditlog "
                "SET actor_id = NULL, action = %s WHERE id = %s",
                ["tampered", entry.pk],
            )
        self.assertIn("append-only", str(caught.exception).lower())
        entry.refresh_from_db()
        self.assertEqual(entry.action, AuditAction.CREDENTIAL_ACCESS)

    def test_the_carve_out_does_not_allow_reassigning_the_actor(self):
        """Pointing the row at a different person is forgery, not cleanup."""
        actor = User.objects.create_user("c@example.com", "Str0ngPass!phrase")
        other = User.objects.create_user("d@example.com", "Str0ngPass!phrase")
        entry = make_entry(actor=actor, actor_email=actor.email)
        with self.assertRaises(Exception):
            self.raw(
                "UPDATE adminapi_adminauditlog SET actor_id = %s WHERE id = %s",
                [other.pk, entry.pk],
            )
        entry.refresh_from_db()
        self.assertEqual(entry.actor_id, actor.pk)

    def test_the_carve_out_does_not_allow_blanking_the_actor_email(self):
        """The denormalised email is the only identification left once
        the FK goes null, so it is covered by the immutable column set."""
        actor = User.objects.create_user("e@example.com", "Str0ngPass!phrase")
        entry = make_entry(actor=actor, actor_email=actor.email)
        with self.assertRaises(Exception):
            self.raw(
                "UPDATE adminapi_adminauditlog "
                "SET actor_id = NULL, actor_email = '' WHERE id = %s",
                [entry.pk],
            )
        entry.refresh_from_db()
        self.assertEqual(entry.actor_email, "e@example.com")

    def test_every_column_is_covered_by_the_immutable_set(self):
        """A column added later without being added to the trigger's
        list would be quietly editable. This fails when that happens,
        rather than leaving the hole to be found by whoever uses it.
        """
        from adminapi.migrations import __name__ as _  # noqa: F401
        from importlib import import_module

        migration = import_module("adminapi.migrations.0001_initial".replace("0001", "0001"))
        columns = {
            field.column for field in AdminAuditLog._meta.concrete_fields
        }
        covered = set(migration.IMMUTABLE_COLUMNS) | {"id", "actor_id"}
        self.assertEqual(
            columns,
            covered,
            "AdminAuditLog gained or lost a column; update "
            "IMMUTABLE_COLUMNS in adminapi/migrations/0001_initial.py "
            "and add a migration that recreates the trigger.",
        )

    def test_the_hard_floor_sits_below_the_application_floor(self):
        """If it did not, the trigger would fire during normal retention
        runs and the command would be unusable."""
        from django.conf import settings

        self.assertLess(
            HARD_FLOOR_DAYS,
            int(settings.ADMIN_AUDIT_MIN_RETENTION_DAYS),
        )
        self.assertLess(
            int(settings.ADMIN_AUDIT_MIN_RETENTION_DAYS),
            int(settings.ADMIN_AUDIT_RETENTION_DAYS),
        )


class ActorSurvivalTests(TestCase):
    """Deleting a user must not delete what the user did."""

    def setUp(self):
        self.actor = User.objects.create_user("gone@example.com", "Str0ngPass!phrase")

    def test_the_row_outlives_the_account(self):
        entry = AdminAuditLog.objects.create(
            action=AuditAction.CREDENTIAL_ACCESS,
            actor=self.actor,
            actor_email=self.actor.email,
        )
        self.actor.delete()
        entry.refresh_from_db()
        self.assertTrue(AdminAuditLog.objects.filter(pk=entry.pk).exists())

    def test_the_email_survives_so_the_actor_is_still_identifiable(self):
        """SET_NULL alone would leave a row that says someone did
        something and cannot say who. The denormalised copy is the whole
        reason there are two columns."""
        entry = AdminAuditLog.objects.create(
            action=AuditAction.SET_PASSWORD,
            actor=self.actor,
            actor_email=self.actor.email,
        )
        self.actor.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.actor_email, "gone@example.com")

    def test_a_cascade_would_have_erased_the_history(self):
        """Stated as a test because it is the actual risk: with CASCADE,
        anyone holding user-delete rights could erase their own trail by
        deleting their own account."""
        for _ in range(3):
            AdminAuditLog.objects.create(
                action=AuditAction.CREDENTIAL_ACCESS,
                actor=self.actor,
                actor_email=self.actor.email,
            )
        self.actor.delete()
        self.assertEqual(AdminAuditLog.objects.count(), 3)

    def test_the_trail_is_still_queryable_by_email_after_deletion(self):
        AdminAuditLog.objects.create(
            action=AuditAction.LOGIN, actor=self.actor, actor_email=self.actor.email
        )
        self.actor.delete()
        self.assertEqual(
            AdminAuditLog.objects.filter(actor_email="gone@example.com").count(), 1
        )


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class AuditSeamTests(TestCase):
    def test_audit_writes_a_row(self):
        audit("admin_login", target_user_id=7)
        entry = AdminAuditLog.objects.get()
        self.assertEqual(entry.action, "admin_login")
        self.assertEqual(entry.target_type, "user")
        self.assertEqual(entry.target_id, "7")

    def test_audit_still_emits_the_log_line(self):
        """Both destinations, not one instead of the other: the stream is
        the copy an attacker owning this host cannot reach, the table is
        the copy that survives an unconfigured handler."""
        with self.assertLogs("proxycrack.audit", level="INFO") as logs:
            audit("admin_login", target_user_id=7)
        self.assertIn("admin_login", logs.output[0])
        self.assertEqual(AdminAuditLog.objects.count(), 1)

    def test_the_changes_diff_is_stored_as_json(self):
        audit(
            "admin_plan_entitlement_changed",
            plan_id=3,
            changes={"max_proxy_tier": {"from": "free", "to": "premium"}},
        )
        entry = AdminAuditLog.objects.get()
        self.assertEqual(entry.changes["max_proxy_tier"]["to"], "premium")
        self.assertEqual(entry.target_type, "plan")

    def test_a_value_that_is_not_json_serialisable_does_not_lose_the_row(self):
        """A datetime in a payload must become a string, exactly as it
        does in the log line -- not raise and cost us the event."""
        audit("admin_subscription_created", subscription_id=1, expires_at=timezone.now())
        entry = AdminAuditLog.objects.get()
        self.assertIsInstance(entry.payload["expires_at"], str)

    def test_a_persistence_failure_does_not_break_the_request(self):
        """The action already happened. Raising would return a 500 for
        work that was done and would not bring the evidence back.

        The failure is logged at CRITICAL on the audit logger -- the
        channel most likely to be shipped off-host -- so "the audit table
        stopped accepting writes" is itself alertable rather than silent.
        """
        from unittest import mock

        from django.db import DatabaseError

        with mock.patch(
            "adminapi.models.AdminAuditLog.objects.create",
            side_effect=DatabaseError("table is gone"),
        ):
            with self.assertLogs("proxycrack.audit", level="CRITICAL") as logs:
                payload = audit("admin_login", target_user_id=7)

        self.assertTrue(
            any("audit_persist_failed" in line for line in logs.output), logs.output
        )
        # The caller still gets its payload back and nothing propagates.
        self.assertEqual(payload["event"], "admin_login")
        self.assertEqual(AdminAuditLog.objects.count(), 0)

    def test_persistence_can_be_switched_off_without_losing_the_log_line(self):
        with override_settings(ADMIN_AUDIT_PERSIST=False):
            with self.assertLogs("proxycrack.audit", level="INFO"):
                audit("admin_login", target_user_id=7)
        self.assertEqual(AdminAuditLog.objects.count(), 0)

    def test_target_inference_prefers_the_most_specific_key(self):
        self.assertEqual(target_from({"target_user_id": 1, "proxy_id": 2})[0], "user")
        self.assertEqual(target_from({"proxy_id": 2})[0], "proxy")
        self.assertEqual(target_from({"batch_id": 9}), ("upload_batch", "9"))
        self.assertEqual(target_from({}), ("", ""))


class ScrubbingTests(TestCase):
    """Audit the access, never the value."""

    def test_every_secret_hint_is_redacted(self):
        for hint in SECRET_KEY_HINTS:
            with self.subTest(hint=hint):
                self.assertEqual(scrub({hint: "s3cret"})[hint], "[redacted]")

    def test_redaction_is_case_insensitive_and_matches_substrings(self):
        self.assertEqual(scrub({"Proxy_Password": "s"})["Proxy_Password"], "[redacted]")
        self.assertEqual(scrub({"API_KEY": "s"})["API_KEY"], "[redacted]")

    def test_nested_values_are_scrubbed(self):
        scrubbed = scrub({"outer": {"password": "s3cret"}})
        self.assertEqual(scrubbed["outer"]["password"], "[redacted]")

    def test_values_inside_lists_are_scrubbed(self):
        scrubbed = scrub({"rows": [{"password": "s3cret"}]})
        self.assertEqual(scrubbed["rows"][0]["password"], "[redacted]")

    def test_an_actors_email_is_not_treated_as_a_secret(self):
        """It is the identity the whole trail is built on."""
        self.assertEqual(scrub({"actor_email": "a@b.c"})["actor_email"], "a@b.c")

    def test_a_proxy_username_is_treated_as_a_secret(self):
        """Half of a paid credential, unlike an actor's email."""
        self.assertEqual(scrub({"username": "paiduser"})["username"], "[redacted]")

    def test_deep_nesting_is_bounded_rather_than_walked_forever(self):
        payload = current = {}
        for _ in range(20):
            current["next"] = {}
            current = current["next"]
        self.assertIsNotNone(scrub(payload))

    def test_a_secret_passed_to_audit_never_reaches_the_table(self):
        audit("credential_access", proxy_id=1, password="sup3rs3cret")
        entry = AdminAuditLog.objects.get()
        self.assertNotIn("sup3rs3cret", json.dumps(entry.payload))
        self.assertEqual(entry.payload["password"], "[redacted]")


# ---------------------------------------------------------------------------
# Coverage: the events the threat model requires, through the real endpoints
# ---------------------------------------------------------------------------


class AuditedEndpointCoverageTests(TestCase):
    """Section 6.3's required events, driven through the API.

    Deliberately not by calling `audit()` -- that would prove the seam
    works and say nothing about whether the views reach it. These go
    through the HTTP layer so a view that stopped auditing would fail
    here.
    """

    def setUp(self):
        # The user agent is a client default rather than something set
        # after login, so the login request itself carries it -- that is
        # the event most worth having it on.
        self.client = APIClient(HTTP_USER_AGENT="ProxyCrackConsole/1.0")
        self.superuser = User.objects.create_superuser(
            "root@example.com", "Str0ngPass!phrase"
        )
        self.plan = Plan.objects.create(
            code="free",
            name="Free",
            max_chain_length=1,
            max_proxy_tier=ProxyTier.FREE,
            is_free_tier=True,
            duration_days=0,
        )
        self.paid_plan = Plan.objects.create(
            code="pro",
            name="Pro",
            max_chain_length=4,
            max_proxy_tier=ProxyTier.PREMIUM,
            duration_days=30,
            sort_order=1,
        )
        self.proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.5",
            port=1080,
            username="paid-user",
            password="paid-password",
            tier=ProxyTier.PREMIUM,
        )
        self.login()

    def login(self):
        response = self.client.post(
            reverse("adminapi:auth-login"),
            {"email": "root@example.com", "password": "Str0ngPass!phrase"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
        )

    def actions(self):
        return set(AdminAuditLog.objects.values_list("action", flat=True))

    # -- authentication ---------------------------------------------------

    def test_login_success_is_recorded(self):
        self.assertIn(AuditAction.LOGIN, self.actions())

    def test_login_failure_is_recorded_with_the_ip(self):
        self.client.post(
            reverse("adminapi:auth-login"),
            {"email": "root@example.com", "password": "wrong"},
            format="json",
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.LOGIN_FAILED)
        self.assertTrue(entry.ip)

    def test_a_failed_login_does_not_record_the_attempted_password(self):
        """The most obvious place a secret would leak into the trail."""
        self.client.post(
            reverse("adminapi:auth-login"),
            {"email": "root@example.com", "password": "hunter2-attempt"},
            format="json",
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.LOGIN_FAILED)
        self.assertNotIn("hunter2-attempt", json.dumps(entry.payload))

    def test_the_user_agent_is_captured(self):
        entry = AdminAuditLog.objects.filter(action=AuditAction.LOGIN).first()
        self.assertEqual(entry.user_agent, "ProxyCrackConsole/1.0")

    # -- credentials ------------------------------------------------------

    def test_a_credential_reveal_is_recorded_without_the_credential(self):
        response = self.client.post(
            reverse("adminapi:proxy-reveal", args=[self.proxy.pk])
        )
        self.assertEqual(response.status_code, 200)
        entry = AdminAuditLog.objects.get(action=AuditAction.CREDENTIAL_ACCESS)
        self.assertEqual(entry.target_type, "proxy")
        self.assertEqual(entry.target_id, str(self.proxy.pk))
        # The disclosure is recorded; the disclosed value is not.
        blob = json.dumps(entry.payload) + json.dumps(entry.changes)
        self.assertNotIn("paid-password", blob)
        self.assertNotIn("paid-user", blob)

    # -- privilege and account --------------------------------------------

    def test_a_privilege_change_is_recorded_with_before_and_after(self):
        target = User.objects.create_user("x@example.com", "Str0ngPass!phrase")
        self.client.patch(
            reverse("adminapi:user-detail", args=[target.pk]),
            {"is_staff": True},
            format="json",
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.USER_PRIVILEGES_CHANGED)
        self.assertEqual(entry.changes["is_staff"], {"from": False, "to": True})
        self.assertEqual(entry.target_id, str(target.pk))

    def test_a_password_reset_is_recorded(self):
        target = User.objects.create_user("y@example.com", "Str0ngPass!phrase")
        self.client.post(
            reverse("adminapi:user-set-password", args=[target.pk]),
            {"password": "An0ther!Str0ngPass"},
            format="json",
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.SET_PASSWORD)
        self.assertEqual(entry.target_id, str(target.pk))
        self.assertNotIn("An0ther!Str0ngPass", json.dumps(entry.payload))

    # -- entitlement, the highest-value event -----------------------------

    def test_a_plan_entitlement_change_is_recorded(self):
        """V5 / T3: one PATCH here re-entitles the whole free user base."""
        self.client.patch(
            reverse("adminapi:plan-detail", args=[self.plan.pk]),
            {"max_proxy_tier": ProxyTier.PREMIUM},
            format="json",
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.PLAN_ENTITLEMENT_CHANGED)
        self.assertEqual(
            entry.changes["max_proxy_tier"], {"from": "free", "to": "premium"}
        )

    # -- subscriptions ----------------------------------------------------

    def test_a_subscription_write_is_recorded(self):
        target = User.objects.create_user("z@example.com", "Str0ngPass!phrase")
        self.client.post(
            reverse("adminapi:subscription-list"),
            {"user": target.pk, "plan": self.paid_plan.pk, "status": "active"},
            format="json",
        )
        self.assertIn(AuditAction.SUBSCRIPTION_CREATED, self.actions())

    def test_a_subscription_cancellation_is_recorded(self):
        target = User.objects.create_user("w@example.com", "Str0ngPass!phrase")
        subscription = Subscription.objects.create(
            user=target, plan=self.paid_plan, status=SubscriptionStatus.ACTIVE
        )
        self.client.post(
            reverse("adminapi:subscription-cancel", args=[subscription.pk])
        )
        self.assertIn(AuditAction.SUBSCRIPTION_CANCELLED, self.actions())

    # -- bulk and import --------------------------------------------------

    def test_a_bulk_operation_is_recorded_with_the_full_id_list(self):
        self.client.post(
            reverse("adminapi:proxy-bulk-enable"),
            {"ids": [self.proxy.pk], "is_enabled": False},
            format="json",
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.BULK_ENABLE)
        self.assertEqual(entry.payload["proxy_ids"], [self.proxy.pk])

    def test_a_csv_upload_is_recorded(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile(
            "proxies.csv",
            b"protocol,host,port,username,password,ss_method,country,city,tier\n"
            b"socks5,203.0.113.77,1080,u,p,,NL,,free\n",
            content_type="text/csv",
        )
        self.client.post(
            reverse("adminapi:proxy-upload-csv"), {"file": upload}, format="multipart"
        )
        entry = AdminAuditLog.objects.get(action=AuditAction.CSV_UPLOAD)
        self.assertEqual(entry.payload["filename"], "proxies.csv")

    # -- operations -------------------------------------------------------

    @override_settings(PROXY_PROBER="proxies.probing.FakeProber")
    def test_a_probe_run_is_recorded(self):
        self.client.post(
            reverse("adminapi:proxy-probe"), {"ids": [self.proxy.pk]}, format="json"
        )
        self.assertIn(AuditAction.PROBE_RUN, self.actions())

    # -- the whole required set -------------------------------------------

    def test_every_required_event_name_exists_in_the_enum(self):
        """A checklist against section 6.3, so adding an endpoint that
        should be audited and forgetting the name is visible here."""
        required = {
            "credential_access",
            "admin_user_privileges_changed",
            "admin_set_password",
            "admin_plan_entitlement_changed",
            "admin_bulk_enable",
            "admin_bulk_delete",
            "admin_csv_upload",
            "admin_subscription_created",
            "admin_subscription_changed",
            "admin_subscription_cancelled",
            "admin_login",
            "admin_login_failed",
            "admin_probe_run",
        }
        self.assertTrue(required.issubset({value for value, _ in AuditAction.choices}))


# ---------------------------------------------------------------------------
# The read-only endpoint
# ---------------------------------------------------------------------------


class AuditLogEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_superuser(
            "root@example.com", "Str0ngPass!phrase"
        )
        self.other = User.objects.create_user(
            "other@example.com", "Str0ngPass!phrase", is_staff=True
        )
        response = self.client.post(
            reverse("adminapi:auth-login"),
            {"email": "root@example.com", "password": "Str0ngPass!phrase"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        AdminAuditLog.objects.create(
            action=AuditAction.CREDENTIAL_ACCESS,
            actor=self.other,
            actor_email=self.other.email,
            target_type="proxy",
            target_id="42",
        )

    def url(self):
        return reverse("adminapi:auditlog-list")

    def test_staff_can_read_it(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data["count"], 1)

    def test_a_non_staff_token_is_refused(self):
        client = APIClient()
        User.objects.create_user("plain@example.com", "Str0ngPass!phrase")
        response = client.get(self.url())
        self.assertIn(response.status_code, (401, 403))

    def test_it_is_filterable_by_action(self):
        response = self.client.get(self.url(), {"action": "credential_access"})
        self.assertTrue(
            all(
                row["action"] == "credential_access"
                for row in response.data["results"]
            )
        )

    def test_it_is_filterable_by_actor(self):
        response = self.client.get(self.url(), {"actor": self.other.pk})
        self.assertEqual(response.data["count"], 1)

    def test_it_is_filterable_by_actor_email_after_the_account_is_gone(self):
        """The FK is null once the user is deleted, so email is the only
        way left to ask "what did this person do"."""
        self.other.delete()
        response = self.client.get(self.url(), {"actor_email": "other@example.com"})
        self.assertEqual(response.data["count"], 1)
        self.assertTrue(response.data["results"][0]["actor_deleted"])

    def test_it_is_filterable_by_date(self):
        response = self.client.get(
            self.url(),
            {"created_at_after": (timezone.now() - timedelta(days=1)).isoformat()},
        )
        self.assertGreaterEqual(response.data["count"], 1)

    def test_a_date_filter_can_exclude_everything(self):
        response = self.client.get(
            self.url(),
            {"created_at_before": (timezone.now() - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(response.data["count"], 0)

    def test_the_endpoint_offers_no_write_verbs(self):
        """Not a ModelViewSet with permissions tightened: a router
        registers the destroy route for a ModelViewSet whatever the
        permissions say, and `adminapi.checks` verifies staff access, not
        verb set -- so the route would exist, reachable by exactly the
        staff account the trail is meant to hold to account."""
        entry = AdminAuditLog.objects.first()
        detail = reverse("adminapi:auditlog-detail", args=[entry.pk])
        self.assertEqual(self.client.delete(detail).status_code, 405)
        self.assertEqual(
            self.client.patch(detail, {"action": "x"}, format="json").status_code, 405
        )
        self.assertEqual(
            self.client.post(self.url(), {"action": "x"}, format="json").status_code,
            405,
        )

    def test_reading_the_audit_log_does_not_itself_fill_the_audit_log(self):
        """Otherwise one operator opening the page buries the events."""
        before = AdminAuditLog.objects.count()
        self.client.get(self.url())
        self.assertEqual(AdminAuditLog.objects.count(), before)


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


class PruneAuditLogTests(TestCase):
    def run_command(self, *args, **options):
        out, err = StringIO(), StringIO()
        call_command("prune_audit_log", *args, stdout=out, stderr=err, **options)
        return out.getvalue()

    def test_it_does_nothing_without_yes(self):
        """A dry run is the default, not a flag. A command whose
        destructive behaviour is what you get by forgetting an argument
        eventually destroys something by accident."""
        aged_entry(400)
        output = self.run_command()
        self.assertIn("DRY RUN", output)
        self.assertEqual(AdminAuditLog.objects.count(), 1)

    def test_it_deletes_with_yes(self):
        aged_entry(400)
        self.run_command("--yes")
        self.assertEqual(
            AdminAuditLog.objects.exclude(action=AuditAction.AUDIT_LOG_PRUNED).count(),
            0,
        )

    def test_it_keeps_rows_inside_the_window(self):
        recent = aged_entry(10)
        self.run_command("--yes")
        self.assertTrue(AdminAuditLog.objects.filter(pk=recent.pk).exists())

    def test_the_default_retention_is_far_longer_than_the_probe_retention(self):
        """Probe rows are telemetry; audit rows are evidence, and the
        questions asked of them are about the distant past."""
        from django.conf import settings

        self.assertGreaterEqual(
            int(settings.ADMIN_AUDIT_RETENTION_DAYS),
            10 * int(settings.PROXY_PROBE_RETENTION_DAYS),
        )

    def test_it_refuses_to_prune_below_the_floor(self):
        """The realistic attack is not "delete everything" -- that is
        conspicuous -- but "run retention with a small number and call it
        a typo"."""
        with self.assertRaises(CommandError) as caught:
            self.run_command("--older-than-days", "5", "--yes")
        self.assertIn("retention floor", str(caught.exception))

    def test_the_refusal_happens_before_anything_is_deleted(self):
        aged_entry(400)
        with self.assertRaises(CommandError):
            self.run_command("--older-than-days", "5", "--yes")
        self.assertEqual(AdminAuditLog.objects.count(), 1)

    def test_the_floor_is_configurable_for_an_operator_who_means_it(self):
        aged_entry(60)
        with override_settings(ADMIN_AUDIT_MIN_RETENTION_DAYS=45):
            self.run_command("--older-than-days", "50", "--yes")
        self.assertEqual(
            AdminAuditLog.objects.exclude(action=AuditAction.AUDIT_LOG_PRUNED).count(),
            0,
        )

    def test_the_prune_records_itself(self):
        """The record of the deletion is written after it, so it is
        outside its own blast radius."""
        aged_entry(400)
        self.run_command("--yes")
        entry = AdminAuditLog.objects.get(action=AuditAction.AUDIT_LOG_PRUNED)
        self.assertEqual(entry.payload["deleted"], 1)
        self.assertEqual(entry.payload["via"], "prune_audit_log")

    def test_the_prune_record_is_not_deleted_by_its_own_run(self):
        aged_entry(400)
        self.run_command("--yes")
        self.run_command("--yes")
        self.assertTrue(
            AdminAuditLog.objects.filter(action=AuditAction.AUDIT_LOG_PRUNED).exists()
        )

    def test_it_batches_large_deletions(self):
        for index in range(7):
            aged_entry(400, target_id=str(index))
        self.run_command("--yes", "--batch-size", "2")
        self.assertEqual(
            AdminAuditLog.objects.exclude(action=AuditAction.AUDIT_LOG_PRUNED).count(),
            0,
        )

    def test_a_dry_run_reports_the_shape_of_what_would_go(self):
        aged_entry(400, action="credential_access")
        output = self.run_command(verbosity=2)
        self.assertIn("credential_access", output)


class MobileCredentialDisclosureAuditTests(TestCase):
    """The busiest credential disclosure path, and it is not the console.

    D18 has a client fetch credentials from `/api/proxies/{id}/` once per
    hop on every connect and every failover, so most paid credentials
    leave the server through the *mobile* API. That path was recording a
    log line and nothing else, which left the durable trail covering the
    rare disclosure and missing the common one.

    Nothing a client can observe changed: same response, same status, same
    throttle. Only the audit row is new.
    """

    def setUp(self):
        self.client = APIClient()
        self.plan = Plan.objects.create(
            code="pro",
            name="Pro",
            max_chain_length=4,
            max_proxy_tier=ProxyTier.PREMIUM,
            duration_days=30,
        )
        self.user = User.objects.create_user("rider@example.com", "Str0ngPass!phrase")
        Subscription.objects.create(
            user=self.user, plan=self.plan, status=SubscriptionStatus.ACTIVE
        )
        self.proxy = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.9",
            port=1080,
            username="paid-user",
            password="paid-password",
            tier=ProxyTier.PREMIUM,
        )
        self.client.force_authenticate(self.user)

    def test_a_mobile_credential_fetch_is_recorded(self):
        response = self.client.get(
            reverse("api:proxy-detail", args=[self.proxy.pk])
        )
        self.assertEqual(response.status_code, 200)
        entry = AdminAuditLog.objects.get(action=AuditAction.CREDENTIAL_ACCESS)
        self.assertEqual(entry.target_type, "proxy")
        self.assertEqual(entry.target_id, str(self.proxy.pk))
        self.assertEqual(entry.payload["via"], "mobile_retrieve")
        self.assertEqual(entry.actor_id, self.user.pk)

    def test_the_credential_itself_is_not_recorded(self):
        """The response carries the credentials; the audit row must not."""
        response = self.client.get(
            reverse("api:proxy-detail", args=[self.proxy.pk])
        )
        self.assertEqual(response.data["password"], "paid-password")
        entry = AdminAuditLog.objects.get(action=AuditAction.CREDENTIAL_ACCESS)
        blob = json.dumps(entry.payload) + json.dumps(entry.changes)
        self.assertNotIn("paid-password", blob)
        self.assertNotIn("paid-user", blob)

    def test_the_phase_1_log_line_is_unchanged(self):
        """Existing tooling greps this format; it is a contract."""
        with self.assertLogs("proxycrack.api", level="INFO") as logs:
            self.client.get(reverse("api:proxy-detail", args=[self.proxy.pk]))
        self.assertTrue(
            any(
                f"credential_access user={self.user.pk} "
                f"proxy={self.proxy.pk} tier=premium" in line
                for line in logs.output
            ),
            logs.output,
        )

    def test_a_listing_is_not_a_disclosure(self):
        """The list serializer carries no credentials, so listing must
        not fire a disclosure event -- otherwise the trail fills with
        page views and the real disclosures are buried."""
        self.client.get(reverse("api:proxy-list"))
        self.assertEqual(
            AdminAuditLog.objects.filter(
                action=AuditAction.CREDENTIAL_ACCESS
            ).count(),
            0,
        )

    def test_a_refused_fetch_records_no_disclosure(self):
        """Nothing was disclosed, so nothing is recorded as disclosed."""
        hidden = Proxy.objects.create(
            protocol=ProxyProtocol.SOCKS5,
            host="198.51.100.10",
            port=1080,
            tier=ProxyTier.PREMIUM,
            is_enabled=False,
        )
        response = self.client.get(reverse("api:proxy-detail", args=[hidden.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            AdminAuditLog.objects.filter(
                action=AuditAction.CREDENTIAL_ACCESS
            ).count(),
            0,
        )
