"""The durable admin audit log, and the database-level append-only guard (P9).

D12 replaced Django Admin with the SPA and lost
`django.contrib.admin.LogEntry` with it. Since then every audit event has
been a log record only: readable by any staff account, deletable by a
superuser, and gone entirely if nobody configured a handler that ships it
off the host. This table is where those events land instead.

Why there is raw SQL in here
----------------------------

`AdminAuditLog` refuses updates and deletes at the model and at the
queryset, which covers every path through the ORM. It does not cover
`manage.py dbshell`, a migration written later, a reporting job with
write credentials, or an ORM escape hatch that has not been invented yet
-- and "append-only" that a single `UPDATE` statement defeats is a
convention with good manners, not a control.

So the rule is also a trigger:

* **UPDATE is refused, with exactly one exception.** There is no
  legitimate edit to an audit row: if a recorded event was wrong, the
  correction is another event, and the fact that the first one was
  recorded is itself evidence.

  The exception is the `SET_NULL` that Django issues against `actor_id`
  when a user is deleted -- which is an `UPDATE`, and which a blanket ban
  would break. Without the carve-out, deleting any user who had ever
  touched the console would fail, and the obvious "fix" would be to
  weaken the FK to `CASCADE` and erase their history with them: the exact
  outcome this table exists to prevent.

  So the carve-out is written as narrowly as it can be. The only update
  that passes is one that sets `actor_id` from non-null to NULL **and
  changes nothing else**. Nulling the actor while also editing the action
  is refused, as is setting `actor_id` to a different user.

* **DELETE is refused for rows younger than `HARD_FLOOR_DAYS` (30).**
  Deletes cannot be banned outright -- retention has to remove something
  eventually -- so the database enforces the part that is never
  legitimate: erasing the recent past. The application's own floor
  (`ADMIN_AUDIT_MIN_RETENTION_DAYS`, 90 days) sits well above this, so
  `prune_audit_log` never comes near the trigger; it fires only for
  something going around the command.

  30 days is deliberately not configurable from the environment. The
  point of this layer is to be the one number an attacker with
  application-level access cannot move, and a setting they could rewrite
  would not be that.

Known interaction, stated so it is not a mystery later
------------------------------------------------------

`TransactionTestCase` (and `manage.py flush`) empty every table with
`DELETE FROM`, which the delete trigger will refuse for any audit row
younger than 30 days. Today no `TransactionTestCase` in this project
reaches the audit seam, so nothing exists to be deleted and the row-level
trigger never fires. A future one that does audit something will fail at
teardown with the trigger's message, which names this file.

Backends
--------

SQLite and PostgreSQL are implemented, matching the two this project is
configured for (`DB_ENGINE`). Anything else gets the ORM-level guards
only and a warning at migrate time, rather than a silent claim of
protection that is not there.

The PostgreSQL branch needs **11 or newer** for `EXECUTE FUNCTION`; on 10
and earlier the spelling is `EXECUTE PROCEDURE`. It has been reviewed but
**not executed** -- this project's development database is SQLite, so the
SQLite branch is the one the test suite exercises. Run the migration
against a PostgreSQL instance before relying on the database layer there,
and `adminapi/test_audit_log.py::DatabaseTriggerTests` is the suite that
will tell you whether it works, unchanged, once `DB_ENGINE=postgres`.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

# Mirrors adminapi.models.HARD_FLOOR_DAYS. Spelled out as a literal here
# because a migration must not import application code -- the model it
# describes can change underneath it.
HARD_FLOOR_DAYS = 30

TABLE = "adminapi_adminauditlog"

UPDATE_MESSAGE = (
    "AdminAuditLog is append-only: UPDATE refused by the database. "
    "Record a correcting event instead."
)
DELETE_MESSAGE = (
    f"AdminAuditLog: refusing to delete audit rows younger than "
    f"{HARD_FLOOR_DAYS} days. Retention runs through "
    f"`manage.py prune_audit_log`."
)

# Every column except `id` and `actor_id`. Listed out rather than
# generated, because the trigger is a security control and a loop that
# silently omits a column it did not know about would leave that column
# editable. A column added later must be added here in the same migration
# that adds it -- `AppendOnlyModelTests` asserts the table's shape so the
# omission fails a test rather than passing quietly.
IMMUTABLE_COLUMNS = (
    "actor_email",
    "action",
    "target_type",
    "target_id",
    "changes",
    "payload",
    "ip",
    "user_agent",
    "created_at",
)

# The single permitted update: Django's SET_NULL when a user row goes.
# `IS` in SQLite and `IS NOT DISTINCT FROM` in PostgreSQL are the
# null-safe comparisons; plain `=` would evaluate to NULL for a null
# column and let the whole condition collapse to "not true", refusing a
# legitimate delete.
SQLITE_ACTOR_UNSET = " AND ".join(
    ["NEW.actor_id IS NULL", "OLD.actor_id IS NOT NULL"]
    + [f"NEW.{column} IS OLD.{column}" for column in IMMUTABLE_COLUMNS]
)

POSTGRES_ACTOR_UNSET = " AND ".join(
    ["NEW.actor_id IS NULL", "OLD.actor_id IS NOT NULL"]
    + [
        f"NEW.{column} IS NOT DISTINCT FROM OLD.{column}"
        for column in IMMUTABLE_COLUMNS
    ]
)


SQLITE_FORWARD = [
    f"""
    CREATE TRIGGER {TABLE}_no_update
    BEFORE UPDATE ON {TABLE}
    FOR EACH ROW WHEN NOT ({SQLITE_ACTOR_UNSET})
    BEGIN
        SELECT RAISE(ABORT, '{UPDATE_MESSAGE}');
    END;
    """,
    # SQLite stores Django datetimes as 'YYYY-MM-DD HH:MM:SS[.ffffff]' in
    # UTC with no offset, and `datetime('now', ...)` produces the same
    # layout, so a plain string comparison orders them correctly.
    f"""
    CREATE TRIGGER {TABLE}_no_recent_delete
    BEFORE DELETE ON {TABLE}
    FOR EACH ROW WHEN OLD.created_at > datetime('now', '-{HARD_FLOOR_DAYS} days')
    BEGIN
        SELECT RAISE(ABORT, '{DELETE_MESSAGE}');
    END;
    """,
]

SQLITE_REVERSE = [
    f"DROP TRIGGER IF EXISTS {TABLE}_no_update;",
    f"DROP TRIGGER IF EXISTS {TABLE}_no_recent_delete;",
]


POSTGRES_FORWARD = [
    f"""
    CREATE OR REPLACE FUNCTION {TABLE}_append_only() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'UPDATE' AND NOT ({POSTGRES_ACTOR_UNSET}) THEN
            RAISE EXCEPTION '{UPDATE_MESSAGE}';
        END IF;
        IF TG_OP = 'DELETE' THEN
            IF OLD.created_at > now() - interval '{HARD_FLOOR_DAYS} days' THEN
                RAISE EXCEPTION '{DELETE_MESSAGE}';
            END IF;
            RETURN OLD;
        END IF;
        -- NEW, not OLD. A BEFORE UPDATE trigger returning OLD silently
        -- discards the change rather than applying it, which would make
        -- the permitted SET_NULL above a no-op and leave a dangling
        -- actor_id pointing at a deleted user.
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    f"""
    CREATE TRIGGER {TABLE}_append_only
    BEFORE UPDATE OR DELETE ON {TABLE}
    FOR EACH ROW EXECUTE FUNCTION {TABLE}_append_only();
    """,
]

POSTGRES_REVERSE = [
    f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE};",
    f"DROP FUNCTION IF EXISTS {TABLE}_append_only();",
]


def _run(schema_editor, sqlite, postgres):
    vendor = schema_editor.connection.vendor
    statements = {"sqlite": sqlite, "postgresql": postgres}.get(vendor)
    if statements is None:
        # Said out loud rather than passed over. The ORM guards still
        # apply; the database-level one does not, and anyone running on
        # this backend should know which protections they actually have.
        print(
            f"  adminapi: no append-only trigger for database backend "
            f"{vendor!r}; AdminAuditLog is protected by the ORM guards "
            f"only (see adminapi/migrations/0001_initial.py)."
        )
        return
    for statement in statements:
        schema_editor.execute(statement)


def add_append_only_triggers(apps, schema_editor):
    _run(schema_editor, SQLITE_FORWARD, POSTGRES_FORWARD)


def drop_append_only_triggers(apps, schema_editor):
    _run(schema_editor, SQLITE_REVERSE, POSTGRES_REVERSE)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor_email', models.CharField(blank=True, db_index=True, help_text="Copied at write time so the row survives the user's deletion. Never back-filled from the FK.", max_length=254)),
                ('action', models.CharField(db_index=True, help_text='Stable event name. `AuditAction` lists the required ones; the column is not restricted to them, because dropping an unrecognised event is worse than storing it.', max_length=64)),
                ('target_type', models.CharField(blank=True, db_index=True, help_text='What was acted on: user, proxy, plan, subscription, upload_batch. Blank for events with no single target.', max_length=32)),
                ('target_id', models.CharField(blank=True, help_text='Primary key of the target, as text -- it outlives the row it points at, so it is a record rather than a reference.', max_length=64)),
                ('changes', models.JSONField(blank=True, default=dict, help_text='{field: {from, to}} for the fields that moved. Entitlement and privilege fields only; never a credential.')),
                ('payload', models.JSONField(blank=True, default=dict, help_text='The rest of the structured event, scrubbed of anything that looks like a secret.')),
                ('ip', models.CharField(blank=True, max_length=120)),
                ('user_agent', models.CharField(blank=True, max_length=400)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'admin audit log entry',
                'verbose_name_plural': 'admin audit log',
                'ordering': ('-created_at', '-pk'),
                'indexes': [models.Index(fields=['actor', '-created_at'], name='audit_actor_time_idx'), models.Index(fields=['action', '-created_at'], name='audit_action_time_idx'), models.Index(fields=['target_type', 'target_id'], name='audit_target_idx')],
            },
        ),
        migrations.RunPython(
            add_append_only_triggers,
            drop_append_only_triggers,
            # The guard is about data, not schema; there is nothing to
            # replay onto a fresh database that CreateModel has not
            # already done.
            elidable=False,
        ),
    ]
