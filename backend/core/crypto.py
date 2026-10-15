"""Symmetric encryption for credentials at rest.

The operator's paid proxy credentials are the sellable asset. Every other
control in this codebase (bulk omission, throttling, audit logging) protects
the API surface; this module protects a database dump, a backup, or a
compromised read replica.

Keys come from `settings.PROXY_CRED_KEYS`, which `core.settings` reads from
the `PROXY_CRED_KEY` environment variable as a comma-separated list. The
list is ordered:

    PROXY_CRED_KEY=<new>,<old>

`MultiFernet` encrypts with the **first** key and tries **every** key when
decrypting, so a rotation is a two-step change with no downtime:

    1. deploy with the new key prepended -- old rows still decrypt
    2. run `manage.py rotate_proxy_credentials` to re-encrypt, then drop the
       old key on the next deploy

There is deliberately no plaintext fallback. A missing or malformed key
raises `CredentialKeyError` on first use and is reported by the
`core.checks` system check, because silently writing plaintext would hide
exactly the failure this module exists to prevent.

PRODUCTION NOTE
---------------
An environment variable is the right amount of machinery for a single VPS
and the wrong amount for anything larger: the key sits in the process
environment, in the deploy tool, and usually in a file on disk, and rotating
it means a redeploy. Before this handles real paid credentials at scale,
move key custody to a KMS (AWS KMS, GCP KMS, Vault Transit) and keep this
module's interface -- `get_crypter()` is the only seam that needs to change,
and envelope encryption (KMS-wrapped data key, cached in memory) drops in
behind it without touching the model fields.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

__all__ = [
    "CredentialDecryptionError",
    "CredentialKeyError",
    "decrypt",
    "encrypt",
    "get_crypter",
]

# Fernet framing: 1 version + 8 timestamp + 16 IV + 32 HMAC.
FERNET_OVERHEAD_BYTES = 57
AES_BLOCK_BYTES = 16

# Cache keyed on the key tuple so `override_settings` is honoured in tests
# and a rotation picks up new keys without a stale crypter.
_CRYPTER_CACHE = {}


class CredentialKeyError(ImproperlyConfigured):
    """No usable encryption key is configured."""


class CredentialDecryptionError(Exception):
    """A stored value could not be decrypted with any configured key."""


def _configured_keys():
    return tuple(getattr(settings, "PROXY_CRED_KEYS", ()) or ())


def get_crypter():
    """The process-wide `MultiFernet`. Raises if no key is usable."""
    keys = _configured_keys()
    if not keys:
        raise CredentialKeyError(
            "PROXY_CRED_KEY is not set. Proxy credentials are encrypted at "
            "rest and the application will not start without a key. Generate "
            "one with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )

    cached = _CRYPTER_CACHE.get(keys)
    if cached is not None:
        return cached

    fernets = []
    for index, key in enumerate(keys):
        try:
            fernets.append(Fernet(key))
        except (ValueError, TypeError) as exc:
            raise CredentialKeyError(
                f"PROXY_CRED_KEY entry {index + 1} is not a valid Fernet key "
                f"({exc}). A key is 32 url-safe base64-encoded bytes."
            ) from exc

    crypter = MultiFernet(fernets)
    _CRYPTER_CACHE[keys] = crypter
    return crypter


def encrypt(value):
    """Plaintext str -> Fernet token str. Empty and None pass through."""
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        value = str(value)
    return get_crypter().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token):
    """Fernet token str -> plaintext str. Empty and None pass through."""
    if token is None or token == "":
        return token
    if isinstance(token, str):
        token = token.encode("ascii", errors="strict")
    try:
        return get_crypter().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Stored credential could not be decrypted with any configured "
            "key. Either PROXY_CRED_KEY lost the key this row was written "
            "with, or the column holds unencrypted data."
        ) from exc


def encrypted_max_length(plaintext_max_length, bytes_per_char=4):
    """Column width needed to hold the token for `plaintext_max_length` chars.

    Assumes the worst case of 4 UTF-8 bytes per character so a column sized
    here can never truncate a token, which would destroy the row.
    """
    raw_bytes = plaintext_max_length * bytes_per_char
    # PKCS7 always pads, including when the input is already block-aligned.
    padded = (raw_bytes // AES_BLOCK_BYTES + 1) * AES_BLOCK_BYTES
    total = FERNET_OVERHEAD_BYTES + padded
    return 4 * ((total + 2) // 3)  # base64
