"""System checks.

`manage.py check` runs before `runserver`, `migrate` and the test runner, so
a missing or malformed credential key stops the process at startup instead
of surfacing later as an unreadable proxy row.

The deploy-tagged checks below run only under `manage.py check --deploy`.
They exist because this project's dangerous defaults are *convenient* ones:
`DEBUG` defaults to True and `SECRET_KEY` to a published placeholder, so a
deploy with a missing `.env` comes up working, reachable, and forgeable.
`PROXY_CRED_KEY` already demonstrates the fail-loud pattern; these apply it
to the rest.
"""

from django.core.checks import Error, Tags, Warning, register


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


@register(Tags.security, deploy=True)
def check_throttle_cache_is_shared(app_configs, **kwargs):
    """Throttle counters must be shared across workers.

    DRF keeps rate-limit counters in the `default` cache. On a per-process
    backend an N-worker deployment multiplies every limit by N, nothing is
    shared between hosts, and a restart clears the history. The limit that
    matters most -- the one in front of the paid proxy credentials -- is
    the one this silently disables.
    """
    from django.conf import settings

    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    non_shared = getattr(settings, "NON_SHARED_CACHE_BACKENDS", ())
    if backend not in non_shared:
        return []

    throttles = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_CLASSES", ())
    if not throttles:
        return []

    return [
        Error(
            f"Throttling is enabled but the default cache backend is "
            f"{backend.rsplit('.', 1)[-1]}, which is not shared between "
            f"processes. Every rate limit in the project -- including the "
            f"credential-access limit protecting the paid proxy "
            f"credentials -- is therefore multiplied by the worker count "
            f"and reset on every restart.",
            hint=(
                "Set CACHE_URL=redis://host:6379/1 (or a memcached "
                "host:port) in the environment. See the CACHES block in "
                "core/settings.py."
            ),
            id="core.E002",
        )
    ]


@register(Tags.security, deploy=True)
def check_production_secrets(app_configs, **kwargs):
    """Refuse a deploy running on the development defaults.

    A JWT signed with the published placeholder key authenticates as any
    user id, so this is identity forgery, not a hygiene warning.
    """
    from django.conf import settings

    problems = []

    if settings.SECRET_KEY == "dev-insecure-change-me":
        problems.append(
            Error(
                "DJANGO_SECRET_KEY is still the development placeholder. "
                "JWT_SIGNING_KEY defaults to it, so anyone with this "
                "repository can mint a token for any user id, including a "
                "superuser.",
                hint="Set DJANGO_SECRET_KEY in the environment.",
                id="core.E003",
            )
        )

    if settings.DEBUG:
        problems.append(
            Error(
                "DJANGO_DEBUG is on. Error responses render settings, "
                "environment and SQL, and the HSTS/SSL/secure-cookie "
                "settings in core/settings.py are all gated on it being "
                "off.",
                hint="Set DJANGO_DEBUG=False.",
                id="core.E004",
            )
        )

    if settings.SIMPLE_JWT.get("SIGNING_KEY") == settings.SECRET_KEY:
        problems.append(
            Warning(
                "JWT_SIGNING_KEY is the same value as DJANGO_SECRET_KEY. "
                "Rotating one then forces the other, so a routine secret "
                "rotation logs out every user.",
                hint="Set JWT_SIGNING_KEY to an independent value.",
                id="core.W001",
            )
        )

    if "*" in settings.ALLOWED_HOSTS:
        problems.append(
            Error(
                "ALLOWED_HOSTS contains '*'.",
                hint="List the hostnames this service answers on.",
                id="core.E005",
            )
        )

    return problems
