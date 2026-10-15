"""Widen the credential columns and encrypt the rows already in them.

The data step deliberately uses raw SQL rather than the historical model.
After the `AlterField` operations the model's fields are `EncryptedCharField`,
so reading through the ORM would try to decrypt values that are still
plaintext and fail. Raw SQL bypasses the field converters and lets this
migration see the columns as they actually are.

Reversible: the backwards step decrypts in place before the columns are
narrowed again.
"""

import core.fields
from django.db import migrations

CRED_COLUMNS = ("username", "password", "ss_method")
CHUNK_SIZE = 1000


def _table_name(apps, connection):
    model = apps.get_model("proxies", "Proxy")
    return connection.ops.quote_name(model._meta.db_table)


def _transform_rows(apps, schema_editor, transform):
    connection = schema_editor.connection
    table = _table_name(apps, connection)
    columns = ", ".join(connection.ops.quote_name(column) for column in CRED_COLUMNS)
    assignments = ", ".join(
        f"{connection.ops.quote_name(column)} = %s" for column in CRED_COLUMNS
    )
    pk = connection.ops.quote_name("id")

    with connection.cursor() as read_cursor, connection.cursor() as write_cursor:
        read_cursor.execute(f"SELECT {pk}, {columns} FROM {table}")
        while True:
            rows = read_cursor.fetchmany(CHUNK_SIZE)
            if not rows:
                break
            params = []
            for row in rows:
                row_id, values = row[0], row[1:]
                if not any(values):
                    continue  # every credential column is empty or NULL
                params.append([*(transform(value) for value in values), row_id])
            if params:
                write_cursor.executemany(
                    f"UPDATE {table} SET {assignments} WHERE {pk} = %s", params
                )


def encrypt_existing(apps, schema_editor):
    from core.crypto import encrypt

    _transform_rows(apps, schema_editor, encrypt)


def decrypt_existing(apps, schema_editor):
    from core.crypto import decrypt

    _transform_rows(apps, schema_editor, decrypt)


class Migration(migrations.Migration):

    dependencies = [
        ("proxies", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="proxy",
            name="password",
            field=core.fields.EncryptedCharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="proxy",
            name="ss_method",
            field=core.fields.EncryptedCharField(
                blank=True,
                help_text="Required for protocol=ss, e.g. aes-256-gcm. Ignored otherwise.",
                max_length=64,
                verbose_name="Shadowsocks cipher",
            ),
        ),
        migrations.AlterField(
            model_name="proxy",
            name="username",
            field=core.fields.EncryptedCharField(blank=True, max_length=255),
        ),
        migrations.RunPython(
            encrypt_existing,
            reverse_code=decrypt_existing,
        ),
    ]
