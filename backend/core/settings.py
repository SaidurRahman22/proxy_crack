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
    # local
    "core",
    "proxies",
    "billing",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
    },
}
