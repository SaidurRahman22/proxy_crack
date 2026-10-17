"""CSV bulk import of proxies.

Kept out of `admin.py` so the same routine can back a management command or
a provider sync later without dragging the admin along. Every row is
validated and committed on its own: one bad line never discards the batch.

ABSENT COLUMN != EMPTY VALUE
----------------------------
This distinction is the most important rule in this module. The import runs
through `update_or_create`, so anything placed in `defaults` is written over
the existing row. An earlier version always wrote `username`, `password`
and `ss_method`, which meant a CSV that merely refreshed `country` and
`latency` blanked the credentials of every endpoint it touched. Those
credentials are Fernet-encrypted with no plaintext copy anywhere, so the
loss was **irrecoverable** -- and the operator got a "success" response.

So: a column that is **not in the header** is not data. It is left alone.
A column that is present with an empty cell is a deliberate clear and does
blank the value. `_clean_row` therefore returns only the fields the file
actually carried, and `tier` is further restricted to creates when its cell
is empty, so an import can never silently downgrade an existing row.
"""

import csv
import io
import logging

from django.conf import settings
from django.db import transaction

from proxies.models import Proxy, ProxyProtocol, ProxyTier, UploadBatch

CANONICAL_HEADERS = (
    "protocol",
    "host",
    "port",
    "username",
    "password",
    "ss_method",
    "country",
    "city",
    "tier",
    "provider",
)

REQUIRED_HEADERS = ("protocol", "host", "port")

# Tolerated spellings for the same column.
HEADER_ALIASES = {
    "ip": "host",
    "address": "host",
    "server": "host",
    "hostname": "host",
    "type": "protocol",
    "scheme": "protocol",
    "user": "username",
    "pass": "password",
    "method": "ss_method",
    "cipher": "ss_method",
    "ss_cipher": "ss_method",
    "country_code": "country",
    "vendor": "provider",
    "seller": "provider",
    "source": "provider",
}

PROTOCOL_ALIASES = {
    "ss": ProxyProtocol.SHADOWSOCKS,
    "shadowsocks": ProxyProtocol.SHADOWSOCKS,
    "socks": ProxyProtocol.SOCKS5,
    "socks5": ProxyProtocol.SOCKS5,
    "socks5h": ProxyProtocol.SOCKS5,
    "http": ProxyProtocol.HTTP,
    "https": ProxyProtocol.HTTP,
}

MAX_STORED_ERRORS = 200

# Columns that are only written when the file actually carries them.
OPTIONAL_COLUMNS = (
    "username",
    "password",
    "ss_method",
    "country",
    "city",
    # Who the operator paid. Follows the same absent-vs-empty rule as the
    # rest: a file without the column leaves an existing label alone, a
    # file with an empty cell clears it. Clearing it is not destructive the
    # way clearing a credential is, but a silently blanked provider turns
    # a shared-vendor finding into a false "different vendors", which is
    # the exact reassurance this feature exists to withhold.
    "provider",
)

# Of those, the ones whose loss is irrecoverable.
CREDENTIAL_COLUMNS = ("username", "password", "ss_method")

logger = logging.getLogger("proxycrack.proxies")


class CSVImportError(Exception):
    """The file itself is unusable -- nothing was imported."""


def _normalise_headers(fieldnames):
    headers = []
    for name in fieldnames or []:
        key = (name or "").strip().lower().lstrip("﻿")
        headers.append(HEADER_ALIASES.get(key, key))
    return headers


def _clean_row(row, present):
    """Validate one CSV row.

    `present` is the set of columns the file's header actually declared.

    Returns `(lookup, defaults, create_defaults)`:
      * `lookup`          the endpoint identity (protocol, host, port)
      * `defaults`        written on create **and** update -- only columns
                          the file carried
      * `create_defaults` written on create only -- the fail-safe values for
                          columns the file did not carry

    Raises `ValueError` for a row that cannot be imported at all.
    """
    protocol_raw = (row.get("protocol") or "").strip().lower()
    protocol = PROTOCOL_ALIASES.get(protocol_raw)
    if protocol is None:
        raise ValueError(f"unknown protocol {protocol_raw!r}")

    host = (row.get("host") or "").strip()
    if not host:
        raise ValueError("host is empty")

    port_raw = (row.get("port") or "").strip()
    try:
        port = int(port_raw)
    except ValueError:
        raise ValueError(f"port {port_raw!r} is not a number")
    if not 1 <= port <= 65535:
        raise ValueError(f"port {port} out of range")

    defaults = {}
    create_defaults = {}

    tier_raw = (row.get("tier") or "").strip().lower()
    if tier_raw:
        valid = {str(choice) for choice in ProxyTier.values}
        if tier_raw not in valid:
            raise ValueError(f"unknown tier {tier_raw!r}")
        defaults["tier"] = tier_raw
    else:
        # No tier given, whether the column is absent or the cell is blank.
        # Premium on create, because a mis-tagged import must never
        # over-expose; untouched on update, because a blank cell is not an
        # instruction to re-tier an endpoint the operator already classified.
        create_defaults["tier"] = str(ProxyTier.PREMIUM)

    if protocol == ProxyProtocol.SHADOWSOCKS and "ss_method" in present:
        # Column present but blank is an explicit (and invalid) clear.
        if not (row.get("ss_method") or "").strip():
            raise ValueError("ss_method (cipher) is required for protocol=ss")

    for column in OPTIONAL_COLUMNS:
        if column in present:
            defaults[column] = (row.get(column) or "").strip()

    return (
        {"protocol": protocol, "host": host, "port": port},
        defaults,
        create_defaults,
    )


def import_proxies_from_csv(file_obj, filename, uploaded_by=None, max_rows=None):
    """Parse `file_obj` and create/update Proxy rows.

    Returns the saved `UploadBatch` carrying the row, success and fail counts.
    Raises `CSVImportError` if the file cannot be parsed at all.
    """
    max_rows = max_rows or getattr(settings, "PROXY_CSV_MAX_ROWS", 50000)

    raw = file_obj.read()
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise CSVImportError("File is not UTF-8 encoded.")
    else:
        text = raw

    reader = csv.reader(io.StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration:
        raise CSVImportError("File is empty.")

    headers = _normalise_headers(header_row)
    present = {name for name in headers if name}
    missing = [name for name in REQUIRED_HEADERS if name not in headers]
    if missing:
        raise CSVImportError(
            "Missing required column(s): "
            + ", ".join(missing)
            + ". Expected header: "
            + ",".join(CANONICAL_HEADERS)
        )

    batch = UploadBatch.objects.create(
        uploaded_by=uploaded_by if (uploaded_by and uploaded_by.is_authenticated) else None,
        filename=filename[:255],
    )

    row_count = created = updated = failed = 0
    errors = []

    for line_number, values in enumerate(reader, start=2):
        if not any((value or "").strip() for value in values):
            continue  # blank line
        if row_count >= max_rows:
            errors.append(f"line {line_number}: row limit {max_rows} reached, stopped")
            break

        row_count += 1
        row = dict(zip(headers, values))

        try:
            lookup, defaults, create_defaults = _clean_row(row, present)
        except ValueError as exc:
            failed += 1
            if len(errors) < MAX_STORED_ERRORS:
                errors.append(f"line {line_number}: {exc}")
            continue

        if (
            lookup["protocol"] == ProxyProtocol.SHADOWSOCKS
            and "ss_method" not in present
        ):
            # A Shadowsocks endpoint needs a cipher, but a file that simply
            # does not carry the column must not be forced to re-supply one
            # for rows that already have it. Only a row that would be
            # *created* is rejected. One extra query, only for this case.
            if not Proxy.objects.filter(**lookup).exists():
                failed += 1
                if len(errors) < MAX_STORED_ERRORS:
                    errors.append(
                        f"line {line_number}: ss_method (cipher) is required to "
                        f"create a protocol=ss proxy; the file has no ss_method "
                        f"column"
                    )
                continue

        try:
            with transaction.atomic():
                defaults["upload_batch"] = batch
                _, was_created = Proxy.objects.update_or_create(
                    **lookup,
                    defaults=defaults,
                    create_defaults={**defaults, **create_defaults},
                )
        except Exception as exc:  # database-level rejection of a single row
            failed += 1
            # The exception text can carry the offending row's values, and
            # one of those columns is a password. `UploadBatch.errors` is
            # rendered in the admin SPA and may be exported, so only the
            # exception type is stored; the detail goes to the server log.
            logger.warning(
                "csv_row_rejected batch=%s line=%s host=%s",
                batch.pk,
                line_number,
                lookup.get("host"),
                exc_info=True,
            )
            if len(errors) < MAX_STORED_ERRORS:
                errors.append(
                    f"line {line_number}: database rejected the row "
                    f"({exc.__class__.__name__}); see the server log"
                )
            continue

        if was_created:
            created += 1
        else:
            updated += 1

    if len(errors) >= MAX_STORED_ERRORS:
        errors.append(f"... further errors truncated at {MAX_STORED_ERRORS}")

    batch.row_count = row_count
    batch.created_count = created
    batch.updated_count = updated
    batch.success_count = created + updated
    batch.fail_count = failed
    batch.errors = "\n".join(errors)
    batch.save(
        update_fields=[
            "row_count",
            "created_count",
            "updated_count",
            "success_count",
            "fail_count",
            "errors",
        ]
    )
    return batch
