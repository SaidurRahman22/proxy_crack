"""System checks.

`manage.py check` runs before `runserver`, `migrate` and the test runner, so
a missing or malformed credential key stops the process at startup instead
of surfacing later as an unreadable proxy row.
"""

from django.core.checks import Error, Tags, register


@register(Tags.security)
def check_credential_encryption_key(app_configs, **kwargs):
    """Fail the check suite unless a usable Fernet key is configured."""
    from core.crypto import CredentialKeyError, get_crypter

    try:
        get_crypter()
    except CredentialKeyError as exc:
        return [
            Error(
                str(exc),
                hint=(
                    "Set PROXY_CRED_KEY in backend/.env. It is a "
                    "comma-separated list; the first key encrypts and all of "
                    "them are tried for decryption, which is what makes key "
                    "rotation possible without downtime. See "
                    "backend/.env.example."
                ),
                id="core.E001",
            )
        ]
    return []
