"""The durable audit trail (P9).

D12 replaced Django Admin with the SPA and, in doing so, abandoned
`django.contrib.admin.LogEntry` -- which had been recording every add,
change and delete with an actor, an object and a timestamp. What replaced
it was a structured log stream (`adminapi/audit.py`): better shaped,
queryable, and *not evidence*. Log records go wherever `LOGGING` sends
them, which by default is stdout, where a staff account can read them and
a superuser can delete them. For a console holding the company's entire
sellable asset that is not good enough.

This module is the substrate the events were always supposed to land in.

Append-only, structurally
-------------------------

"Append-only" written in a docstring is a convention, and conventions are
what the next person edits around. So it is enforced at three levels, each
of which would have to be defeated separately:

1. **The model.** `save()` refuses to rewrite an existing row and
   `delete()` refuses outright. Both raise `AuditLogImmutable`.
2. **The queryset.** `update()`, `delete()` and `bulk_update()` raise the
   same thing, so neither `.filter(...).delete()` nor a careless
   `objects.all().update()` has a path through. This is the layer that
   matters most, because a queryset write never loads a model instance and
   would otherwise sail straight past rule 1.
3. **The database.** A trigger rejects every `UPDATE`, and rejects a
   `DELETE` of any row younger than `HARD_FLOOR_DAYS`. That one survives
   raw SQL, a `dbshell` session and any future code that has never heard
   of this module.

The retention command is the single sanctioned way to remove anything, and
it goes through `delete_for_retention` -- a separate, explicitly-named
function rather than a flag on the normal path, so deleting audit history
is something you have to go and find.

What is never written
---------------------

A credential, a password or a token. The rule is to record that a
disclosure happened, not what was disclosed; an audit log containing the
secrets is a second copy of the thing being protected, usually with worse
access control than the first. `adminapi.audit.scrub` enforces it on the
way in and `test_audit_log.py` asserts it.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

__all__ = [
    "AdminAuditLog",
    "AuditAction",
    "AuditLogImmutable",
    "HARD_FLOOR_DAYS",
    "delete_for_retention",
]


# The floor the *database* enforces, below which no delete is possible by
# any route including raw SQL. Deliberately far below the application's
# configurable retention floor (ADMIN_AUDIT_MIN_RETENTION_DAYS, 90 days by
# default) so the trigger never fires during normal operation -- it exists
# to stop someone erasing last week, not to second-guess the policy.
#
# Changing it requires a migration, which is the point: it is the one
# number here that an environment variable cannot move.
HARD_FLOOR_DAYS = 30


class AuditLogImmutable(Exception):
    """Raised by every application path that would mutate the audit log."""


class AuditAction(models.TextChoices):
    """The events the threat model (section 6.3) requires, by name.

    A closed choice list rather than free text, so "every entitlement
    change last quarter" is a query. `action` is **not** constrained to
    these values at the database level, though: an event name that has not
    been added here yet must still be recorded rather than dropped, and
    losing an audit line to a validation error is strictly worse than
    storing one the enum does not know.
    """

    # Credentials -- the asset the business is built on.
    CREDENTIAL_ACCESS = "credential_access", "Credential disclosed"
    # Privilege and account changes.
    USER_PRIVILEGES_CHANGED = "admin_user_privileges_changed", "Privileges changed"
    USER_CREATED = "admin_user_created", "User created"
    USER_DELETED = "admin_user_deleted", "User deleted"
    SET_PASSWORD = "admin_set_password", "Password reset"
    # The single highest-value event in the system (T3 / V5).
    PLAN_ENTITLEMENT_CHANGED = (
        "admin_plan_entitlement_changed",
        "Plan entitlement changed",
    )
    # Subscriptions.
    SUBSCRIPTION_CREATED = "admin_subscription_created", "Subscription created"
    SUBSCRIPTION_CHANGED = "admin_subscription_changed", "Subscription changed"
    SUBSCRIPTION_CANCELLED = "admin_subscription_cancelled", "Subscription cancelled"
    # Bulk and import.
    BULK_ENABLE = "admin_bulk_enable", "Bulk enable/disable"
    BULK_DELETE = "admin_bulk_delete", "Bulk delete"
    CSV_UPLOAD = "admin_csv_upload", "CSV upload"
    # Operations.
    PROBE_RUN = "admin_probe_run", "Probe run"
    # Authentication.
    LOGIN = "admin_login", "Login succeeded"
    LOGIN_FAILED = "admin_login_failed", "Login failed"
    # Console sign-out (P24). Login and login-failure were rows from the
    # start; the end of the session was not, so "when did this staff
    # account last end a session, and from where" had no answer on the
    # one surface that holds the credentials. Prefixed `admin_` because
    # it is the console surface, beside `admin_login`.
    LOGOUT_ADMIN = "admin_logout", "Logout (console)"
    # Mobile sign-out (P17). Unprefixed like `credential_access`, because
    # the `admin_` prefix marks the console surface and this is the
    # mobile one. The row is what answers "when did this account last end
    # a session, and from where" after a phone goes missing.
    LOGOUT = "logout", "Logout"
    # Meta: recorded by `prune_audit_log` after it deletes, so the record
    # of the deletion is itself outside what was deleted.
    AUDIT_LOG_PRUNED = "audit_log_pruned", "Audit log pruned"


class AuditLogQuerySet(models.QuerySet):
    """A queryset with the mutating verbs removed.

    This is the layer that actually holds the line. `save()` guards on the
    model only protect code that loaded an instance; `Model.objects
    .filter(...).delete()` never does, and is both the shortest path to
    destroying the trail and the one a reasonable person writes without
    thinking about it.
    """

    def update(self, **kwargs):
        raise AuditLogImmutable(
            "AdminAuditLog is append-only: an audit row cannot be updated. "
            "If the recorded event was wrong, record a correcting event."
        )

    def bulk_update(self, *args, **kwargs):
        raise AuditLogImmutable("AdminAuditLog is append-only: no bulk_update.")

    def delete(self):
        raise AuditLogImmutable(
            "AdminAuditLog is append-only: rows cannot be deleted from the "
            "application. Retention is enforced by `manage.py "
            "prune_audit_log`, which is the only sanctioned path and "
            "refuses to go below settings.ADMIN_AUDIT_MIN_RETENTION_DAYS."
        )

    # Deliberately private and deliberately ugly to call. The retention
    # command reaches this through `delete_for_retention` below, which is
    # the only caller and is greppable in one search.
    def _delete_for_retention(self):
        return super().delete()


class AuditLogManager(models.Manager.from_queryset(AuditLogQuerySet)):
    """Default manager. Nothing special beyond carrying the queryset."""


class AdminAuditLog(models.Model):
    """One recorded administrative event.

    Written from `adminapi.audit.audit`, which every call site already
    goes through -- so this table gained its coverage without a single
    view being re-instrumented, and a new audited action gets persistence
    for free.
    """

    # SET_NULL, not CASCADE. The entire purpose of an audit trail is to
    # outlive the account it describes: cascading would mean that deleting
    # a user erases the record of everything they did, which hands anyone
    # with user-delete rights a way to erase their own history.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    # Denormalised, for the same reason. After the FK goes null this is
    # the only thing left that identifies who acted.
    actor_email = models.CharField(
        max_length=254,
        blank=True,
        db_index=True,
        help_text="Copied at write time so the row survives the user's "
        "deletion. Never back-filled from the FK.",
    )
    action = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Stable event name. `AuditAction` lists the required "
        "ones; the column is not restricted to them, because dropping an "
        "unrecognised event is worse than storing it.",
    )
    target_type = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
        help_text="What was acted on: user, proxy, plan, subscription, "
        "upload_batch. Blank for events with no single target.",
    )
    target_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Primary key of the target, as text -- it outlives the "
        "row it points at, so it is a record rather than a reference.",
    )
    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text="{field: {from, to}} for the fields that moved. "
        "Entitlement and privilege fields only; never a credential.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="The rest of the structured event, scrubbed of anything "
        "that looks like a secret.",
    )
    ip = models.CharField(max_length=120, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    # `default=timezone.now` rather than `auto_now_add`: the retention
    # tests have to create rows with a chosen age, and `auto_now_add`
    # would leave `objects.update()` -- which this model refuses -- as the
    # only way to do it.
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = AuditLogManager()

    class Meta:
        verbose_name = "admin audit log entry"
        verbose_name_plural = "admin audit log"
        ordering = ("-created_at", "-pk")
        indexes = [
            # The three questions this table is read with: what did this
            # person do, when did this kind of event happen, and what
            # touched this object.
            models.Index(fields=["actor", "-created_at"], name="audit_actor_time_idx"),
            models.Index(fields=["action", "-created_at"], name="audit_action_time_idx"),
            models.Index(
                fields=["target_type", "target_id"], name="audit_target_idx"
            ),
        ]

    def __str__(self):
        who = self.actor_email or "anonymous"
        return f"{self.action} by {who} at {self.created_at:%Y-%m-%d %H:%M:%S}"

    def save(self, *args, **kwargs):
        """Insert only.

        `self.pk is not None` is not the test -- an explicitly-assigned pk
        on a new row would trip it. `self._state.adding` is what Django
        itself uses to tell an insert from an update.
        """
        if not self._state.adding:
            raise AuditLogImmutable(
                "AdminAuditLog is append-only: row "
                f"{self.pk} cannot be modified after it is written."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutable(
            "AdminAuditLog is append-only: row "
            f"{self.pk} cannot be deleted. See `manage.py prune_audit_log`."
        )


def delete_for_retention(queryset):
    """The one sanctioned delete path, used by `prune_audit_log`.

    A free function rather than a method, and named for its only purpose,
    so that `grep -r delete_for_retention` finds every place in the
    codebase that can remove an audit row -- today exactly one. A `force=`
    flag on `delete()` would have been shorter and would have put the
    capability back on the path people reach for by reflex.

    The database trigger still applies underneath this: rows younger than
    `HARD_FLOOR_DAYS` are refused even here.
    """
    return queryset._delete_for_retention()
