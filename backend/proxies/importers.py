"""CSV bulk import of proxies.

Kept out of `admin.py` so the same routine can back a management command or
a provider sync later without dragging the admin along. Every row is
validated and committed on its own: one bad line never discards the batch.
"""

import csv
import io

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


class CSVImportError(Exception):
    """The file itself is unusable -- nothing was imported."""


def _normalise_headers(fieldnames):
    headers = []
    for name in fieldnames or []:
        key = (name or "").strip().lower().lstrip("﻿")
        headers.append(HEADER_ALIASES.get(key, key))
    return headers


def _clean_row(row):
    """Validate one CSV row. Returns (lookup, defaults) or raises ValueError."""
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

    tier_raw = (row.get("tier") or "").strip().lower()
    if tier_raw:
        valid = {str(choice) for choice in ProxyTier.values}
        if tier_raw not in valid:
            raise ValueError(f"unknown tier {tier_raw!r}")
        tier = tier_raw
    else:
        # Default to premium: a mis-tagged import must never over-expose.
        tier = str(ProxyTier.PREMIUM)

    ss_method = (row.get("ss_method") or "").strip()
    if protocol == ProxyProtocol.SHADOWSOCKS and not ss_method:
        raise ValueError("ss_method (cipher) is required for protocol=ss")

    return (
        {"protocol": protocol, "host": host, "port": port},
        {
            "username": (row.get("username") or "").strip(),
            "password": (row.get("password") or "").strip(),
            "ss_method": ss_method,
            "country": (row.get("country") or "").strip(),
            "city": (row.get("city") or "").strip(),
            "tier": tier,
        },
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
            lookup, defaults = _clean_row(row)
        except ValueError as exc:
            failed += 1
            if len(errors) < MAX_STORED_ERRORS:
                errors.append(f"line {line_number}: {exc}")
            continue

        try:
            with transaction.atomic():
                defaults["upload_batch"] = batch
                _, was_created = Proxy.objects.update_or_create(
                    **lookup, defaults=defaults
                )
        except Exception as exc:  # database-level rejection of a single row
            failed += 1
            if len(errors) < MAX_STORED_ERRORS:
                errors.append(f"line {line_number}: {exc.__class__.__name__}: {exc}")
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
