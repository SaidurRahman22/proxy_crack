"""Remove what `django.contrib.admin` left behind.

Django Admin was unmounted and `django.contrib.admin` removed from
`INSTALLED_APPS` (see `core/urls.py`). That alone is enough for the
application to run, and on a **fresh** database it is the end of the story:
the app's migrations are never loaded, so `django_admin_log`, the
`admin | logentry` content type and its four permissions are simply never
created.

An **existing** database is different, and left alone it would keep three
artefacts forever:

1. the `django_admin_log` table,
2. three rows in `django_migrations` recording `admin.0001`-`0003` as
   applied,
3. the `admin | logentry` content type and its `add/change/delete/view`
   permissions.

This migration removes all three. Why removal rather than
leave-it-and-document-it:

**The migration records are the real hazard, not the table.** A dropped
app's records stay in `django_migrations` and Django is happy to ignore
them -- until somebody re-adds `django.contrib.admin`. Then the loader
reads three applied migrations, concludes the tables exist, creates
nothing, and the admin fails at first use with "no such table:
django_admin_log". That is a trap that springs months later, in whoever's
hands the project is in by then. Clearing the records makes re-adding the
app work correctly: its own migrations simply run. The removal is
therefore reversible at the level that matters -- the config -- which is
exactly why `reverse` below does nothing.

**Nothing is lost.** `django_admin_log` held **0 rows** in the development
database when this was written, and could not have held many elsewhere:
the console it recorded was a fallback that D12 already discouraged, and
`adminapi.AdminAuditLog` (P9) has been the real trail since. Had there
been history in it, the argument would have gone the other way and the
table would have stayed -- destroying evidence to tidy a schema is a bad
trade at any count above zero. The migration therefore refuses to run if
it finds rows, rather than assuming today's fact holds on every database
this is ever applied to.

**A schema that two environments disagree about is a bug waiting.** Fresh
databases never get these objects. Keeping them on older ones means the
same migration state describes two different schemas permanently, which is
precisely the kind of divergence that makes a problem reproduce in one
place and not another.

**The dangling foreign key.** `django_admin_log.user_id` references
`core_user` with no `ON DELETE` clause -- Django handles deletion in
Python, via the app registry. With the app uninstalled, Django's collector
no longer knows the table exists, so a row in it would make
`DELETE /api/admin/users/{id}/` fail with a database-level integrity
error. Inert at zero rows and with no writer left, but it is a constraint
nobody owns.

The content type and its permissions are removed for reason 3 only -- they
are inert -- but they are the same orphan class, and `manage.py
remove_stale_contenttypes` is interactive, so leaving them means leaving a
chore. Nothing in this project grants them (checked: zero rows in
`core_user_user_permissions` and `auth_group_permissions` referenced
them), and a permission naming a model that cannot be imported cannot be
meaningfully held anyway.

Every step is conditional and idempotent: on a fresh database this
migration finds nothing and does nothing.
"""

from django.db import migrations

ADMIN_APP_LABEL = "admin"
LOG_TABLE = "django_admin_log"


def _table_exists(connection, table):
    with connection.cursor() as cursor:
        return table in connection.introspection.table_names(cursor)


def drop_admin_leftovers(apps, schema_editor):
    connection = schema_editor.connection

    if _table_exists(connection, LOG_TABLE):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(LOG_TABLE)}")
            rows = cursor.fetchone()[0]
        if rows:
            # Deliberately fatal. The point of this migration is that the
            # table is empty; if it is not, the operator is about to lose
            # an audit trail to a cleanup, and that decision is theirs to
            # make explicitly.
            raise RuntimeError(
                f"{LOG_TABLE} holds {rows} row(s). This migration drops the "
                f"table and will not silently destroy administrative "
                f"history. Export the rows (or decide they are worthless) "
                f"and empty the table, then re-run migrate."
            )
        schema_editor.execute(
            f"DROP TABLE IF EXISTS {connection.ops.quote_name(LOG_TABLE)}"
        )

    # The content type, and -- by cascade -- its four permissions and any
    # group/user grants of them. Uses the historical models so this is the
    # project state's `auth`/`contenttypes`, not whatever is imported.
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label=ADMIN_APP_LABEL, model="logentry").delete()

    # Last, because the two steps above are what these records claim to
    # have done. Removing them is what makes re-adding
    # `django.contrib.admin` a working config change rather than a broken
    # one.
    from django.db.migrations.recorder import MigrationRecorder

    MigrationRecorder(connection).migration_qs.filter(app=ADMIN_APP_LABEL).delete()


def noop(apps, schema_editor):
    """Reversing this migration correctly does nothing.

    There is no `django_admin_log` to recreate from here: the model lives
    in `django.contrib.admin`, which is not installed. The supported way
    back is to re-add the app to `INSTALLED_APPS` and run `migrate` --
    which works precisely because `drop_admin_leftovers` cleared the
    recorded migrations, so Django runs the admin's own three migrations
    and rebuilds the table, the content type and the permissions itself.
    """


class Migration(migrations.Migration):
    # `core` owns `User`, the target of the dangling foreign key this
    # removes, and is the project app. `adminapi`'s chain is the audit
    # log's -- including its append-only triggers -- and is deliberately
    # left untouched.
    dependencies = [
        ("core", "0001_initial"),
        # So the historical ContentType/Permission models are in the state
        # when `drop_admin_leftovers` runs, and their tables exist.
        ("contenttypes", "__first__"),
        ("auth", "__first__"),
    ]

    operations = [
        migrations.RunPython(drop_admin_leftovers, noop),
    ]
