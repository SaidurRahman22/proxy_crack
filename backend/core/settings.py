"""
Django settings for the Proxy Crack backend.

Everything environment-specific is read from `.env` via python-dotenv so that
moving from the development SQLite database to PostgreSQL is a config change,
not a code change. See `.env.example` for the full list of variables.
"""

from datetime import timedelta
from pathlib import Path

import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def env_bool(key, default=False):
    return str(env(key, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_int(key, default):
    try:
        return int(str(env(key, str(default))).strip())
    except (TypeError, ValueError):
        return default


def env_list(key, default=""):
    return [item.strip() for item in str(env(key, default)).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    # local
    "core",
    "proxies",
    "billing",
    "api",
    "adminapi",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Must precede CommonMiddleware so a CORS preflight is answered before a
    # redirect (APPEND_SLASH / SSL) can strip the headers off it.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"


# ---------------------------------------------------------------------------
# Database. SQLite for development; set DB_ENGINE=postgres to switch.
# psycopg2-binary is already installed, so the move needs no code change.
# ---------------------------------------------------------------------------

DB_ENGINE = str(env("DB_ENGINE", "sqlite")).strip().lower()

if DB_ENGINE in {"postgres", "postgresql", "psql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "proxycrack"),
            "USER": env("DB_USER", "proxycrack"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "OPTIONS": {"sslmode": env("DB_SSLMODE", "prefer")},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / env("DB_NAME", "db.sqlite3"),
            "OPTIONS": {"timeout": 20},
        }
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": env_int("PASSWORD_MIN_LENGTH", 10)},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/admin/login/"


# ---------------------------------------------------------------------------
# DRF + JWT
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Default deny. Endpoints opt out explicitly, never by omission.
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "api.pagination.StandardResultsPagination",
    "PAGE_SIZE": env_int("API_PAGE_SIZE", 25),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", "30/min"),
        "user": env("THROTTLE_USER", "120/min"),
        # Scoped throttles. The proxy list and especially the credential
        # endpoint are the scrape targets, so they are the tightest.
        "register": env("THROTTLE_REGISTER", "10/hour"),
        "login": env("THROTTLE_LOGIN", "20/hour"),
        "proxy_list": env("THROTTLE_PROXY_LIST", "60/min"),
        "proxy_credentials": env("THROTTLE_PROXY_CREDENTIALS", "60/hour"),
        "chain_write": env("THROTTLE_CHAIN_WRITE", "60/hour"),
        "usage_report": env("THROTTLE_USAGE_REPORT", "120/hour"),
        "content": env("THROTTLE_CONTENT", "60/hour"),
        # --- Admin API ---------------------------------------------------
        # Graded by what the endpoint can be used to take, not by how
        # convenient it is. Dense tables poll, so the generic read budget is
        # loose; credentials, writes, bulk mutation and CSV import each get
        # a scope of their own so the loose number cannot cover them.
        "admin": env("THROTTLE_ADMIN", "1200/hour"),
        # Login, per IP. Counts every attempt.
        "admin_login": env("THROTTLE_ADMIN_LOGIN", "10/min"),
        # Login, per submitted email. Counts FAILURES only, so an operator
        # signing in repeatedly is never locked out but a password guesser
        # is. Two keys because one protects the wrong thing: per-IP alone
        # lets a botnet through, per-email alone lets one attacker lock
        # every admin out.
        "admin_login_email": env("THROTTLE_ADMIN_LOGIN_EMAIL", "20/hour"),
        "admin_refresh": env("THROTTLE_ADMIN_REFRESH", "60/hour"),
        # Credential disclosure -- the anti-scrape control for the asset the
        # business is built on. Two windows: the hourly one bounds a burst,
        # the daily one bounds patience. Hourly limits alone are defeated by
        # waiting; a daily ceiling is not. At 400/day a 50k-row inventory
        # takes four months.
        "admin_proxy_credentials": env("THROTTLE_ADMIN_CREDENTIALS", "120/hour"),
        "admin_proxy_credentials_day": env(
            "THROTTLE_ADMIN_CREDENTIALS_DAY", "400/day"
        ),
        "admin_write": env("THROTTLE_ADMIN_WRITE", "300/hour"),
        # One call can touch 1000 rows.
        "admin_bulk": env("THROTTLE_ADMIN_BULK", "20/hour"),
        # One call is minutes of synchronous worker time.
        "admin_csv_upload": env("THROTTLE_ADMIN_CSV_UPLOAD", "10/hour"),
    },
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_MINUTES", 30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_DAYS", 30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}


# ---------------------------------------------------------------------------
# Cache. This is a throttling control, not a performance setting.
#
# DRF's SimpleRateThrottle keeps its counters in the `default` cache. With
# no CACHES block Django falls back to LocMemCache, which is per-process:
# four gunicorn workers turn a 60/hour limit into 240/hour, nothing is
# shared between hosts, and every deploy hands an attacker a fresh budget.
# The credential throttle is the primary anti-scrape control for the paid
# proxy credentials on BOTH APIs, so it has to count across the whole
# deployment or it is decorative.
#
# Set CACHE_URL=redis://host:6379/1 in any multi-process deployment.
# LocMem is correct for a single-process dev server and for tests, and
# `core.checks.check_throttle_cache_is_shared` fails `check --deploy` if it
# is still in place with DEBUG off.
# ---------------------------------------------------------------------------

CACHE_URL = env("CACHE_URL", "")

if CACHE_URL.startswith(("redis://", "rediss://", "unix://")):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env_list("CACHE_URL") or CACHE_URL,
            "KEY_PREFIX": env("CACHE_KEY_PREFIX", "proxycrack"),
            "TIMEOUT": env_int("CACHE_TIMEOUT", 300),
        }
    }
elif CACHE_URL:
    # memcached: "host:port" or a comma-separated list.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
            "LOCATION": env_list("CACHE_URL"),
            "KEY_PREFIX": env("CACHE_KEY_PREFIX", "proxycrack"),
            "TIMEOUT": env_int("CACHE_TIMEOUT", 300),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "proxycrack-local",
        }
    }

# Backends whose counters are not shared between processes. Read by the
# deploy check; kept here so the list is next to the configuration it
# describes.
NON_SHARED_CACHE_BACKENDS = (
    "django.core.cache.backends.locmem.LocMemCache",
    "django.core.cache.backends.dummy.DummyCache",
    "django.core.cache.backends.filebased.FileBasedCache",
)


# ---------------------------------------------------------------------------
# Entitlement safety net.
# Used only when no free Plan row exists. The API must never fail open.
# ---------------------------------------------------------------------------

ENTITLEMENT_FALLBACK = {
    "max_chain_length": env_int("FALLBACK_MAX_CHAIN_LENGTH", 1),
    "max_devices": env_int("FALLBACK_MAX_DEVICES", 1),
    "bandwidth_limit_bytes": env_int("FALLBACK_BANDWIDTH_LIMIT_BYTES", 1073741824),
    "proxy_tier": "free",
}


# ---------------------------------------------------------------------------
# Credential encryption at rest.
# Comma-separated Fernet keys: the first encrypts, all are tried to decrypt.
# Validated by core.checks.check_credential_encryption_key -- a missing or
# malformed key fails `manage.py check` rather than falling back to plaintext.
# ---------------------------------------------------------------------------

PROXY_CRED_KEYS = env_list("PROXY_CRED_KEY")


# ---------------------------------------------------------------------------
# CORS -- for the admin SPA only.
#
# Scoped to /api/admin/ by CORS_URLS_REGEX so the mobile API's responses are
# byte-for-byte what they were before. CORS_ALLOW_ALL_ORIGINS is never used:
# this API is unscoped and staff-only, so the origin list stays explicit and
# environment-driven. Credentials are off because the SPA authenticates with
# a Bearer header, not a cookie -- there is nothing for a browser to attach
# to a cross-origin request on its own, which is also what keeps CSRF out of
# scope here.
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ALLOW_CREDENTIALS = env_bool("CORS_ALLOW_CREDENTIALS", False)
CORS_URLS_REGEX = env("CORS_URLS_REGEX", r"^/api/admin/.*$")


# ---------------------------------------------------------------------------
# CSV bulk upload limits (admin proxy import)
# ---------------------------------------------------------------------------

PROXY_CSV_MAX_BYTES = env_int("PROXY_CSV_MAX_BYTES", 5242880)
PROXY_CSV_MAX_ROWS = env_int("PROXY_CSV_MAX_ROWS", 50000)


# ---------------------------------------------------------------------------
# i18n / static
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 10485760)


# ---------------------------------------------------------------------------
# Transport security. Enabled whenever DEBUG is off.
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
        "proxycrack": {
            "level": env("LOG_LEVEL", "INFO"),
            "handlers": ["console"],
            "propagate": False,
        },
        # Admin audit events (adminapi/audit.py). Separated from the
        # application logger so that shipping them off-host -- which is
        # what makes them evidence rather than a convenience -- is a
        # handler change here and nothing else. Always INFO or lower,
        # regardless of LOG_LEVEL: an audit trail that a log-level tweak
        # can silence is not an audit trail.
        #
        # NOT YET DONE: these are log records, not rows in an append-only
        # table, and by default they go to stdout where a staff account can
        # read them and a superuser can delete them.
        "proxycrack.audit": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}
