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
        # Chain resolution. Looser than a write because a client resolves
        # on every connect and on every failover, but still bounded: a
        # constrained hop is a query against the inventory, and an
        # unthrottled one is an oracle for enumerating it.
        "chain_resolve": env("THROTTLE_CHAIN_RESOLVE", "120/hour"),
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
        # One call opens up to PROXY_PROBE_API_MAX_IDS outbound connections
        # and holds a worker while they complete. Tight on purpose: the
        # scheduled `probe_proxies` command is the normal path, and this
        # endpoint is the "check these twelve right now" button beside it.
        "admin_probe": env("THROTTLE_ADMIN_PROBE", "30/hour"),
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
# Proxy health checking, scoring and failover.
#
# `is_alive` existed in Phase 1 and nothing wrote it. These settings are
# what the prober, the scorer and the chain resolver read. Validated by
# proxies/checks.py -- notably PROXY_PROBE_RETENTION_DAYS must be >=
# PROXY_PROBE_WINDOW_DAYS, or pruning deletes the rows scoring depends on.
# ---------------------------------------------------------------------------

# --- What a probe does -----------------------------------------------------
# The prober opens a real connection THROUGH the proxy to this target and
# times the round trip. Not a TCP handshake against the proxy's own port: a
# listening socket proves nothing about whether traffic still flows.
# Port 443 because many proxies permit CONNECT only to 443, and a probe a
# correctly-configured proxy refuses measures our configuration, not theirs.
PROXY_PROBE_TARGET_HOST = env("PROXY_PROBE_TARGET_HOST", "www.google.com")
PROXY_PROBE_TARGET_PORT = env_int("PROXY_PROBE_TARGET_PORT", 443)

# Whole-handshake budget, not per socket operation.
PROXY_PROBE_TIMEOUT_SECONDS = float(env("PROXY_PROBE_TIMEOUT_SECONDS", "10"))
# Bounded: probing is IO-bound so threads are right, but one thread per row
# exhausts file descriptors long before it exhausts the inventory.
PROXY_PROBE_CONCURRENCY = env_int("PROXY_PROBE_CONCURRENCY", 8)

# Swapped for proxies.probing.FakeProber in every test. Nothing in the test
# suite may touch the network.
PROXY_PROBER = env("PROXY_PROBER", "proxies.probing.SocketProber")

# --- The rolling window ----------------------------------------------------
# "The newest N probes, none older than D days." Bounded on both axes: a
# count alone judges a rarely-probed proxy on last quarter's data, an age
# alone lets one busy afternoon swamp the sample.
PROXY_PROBE_WINDOW = env_int("PROXY_PROBE_WINDOW", 50)
PROXY_PROBE_WINDOW_DAYS = env_int("PROXY_PROBE_WINDOW_DAYS", 7)

# --- Retention -------------------------------------------------------------
# ProxyProbe is the only table that grows without a user action. 10k proxies
# at one probe per five minutes is ~2.9M rows a day. `prune_probes` enforces
# this; see its docstring.
PROXY_PROBE_RETENTION_DAYS = env_int("PROXY_PROBE_RETENTION_DAYS", 30)

# --- Liveness --------------------------------------------------------------
# Consecutive failures before a proxy is marked dead. One timeout is noise.
# Note the asymmetry: N failures to die, a single success to revive. Failing
# slow and recovering fast is deliberate -- a proxy that flaps is punished
# by its success rate, which is a far better signal than a boolean.
PROXY_DEAD_AFTER_FAILURES = env_int("PROXY_DEAD_AFTER_FAILURES", 3)

# --- Scoring (see proxies/health.py for the model) -------------------------
# Latency's maximum influence on the score, as a fraction. At 0.4 latency
# can move a score within a 40% band; reliability multiplies the whole
# thing. Above ~0.6 the model inverts and fast-but-lossy starts winning.
PROXY_LATENCY_WEIGHT = float(env("PROXY_LATENCY_WEIGHT", "0.4"))
# p95 at which the latency factor is exactly 0.5. A smooth curve, not a
# cliff, so a proxy does not change rank for crossing a threshold by 1 ms.
PROXY_LATENCY_HALF_MS = env_int("PROXY_LATENCY_HALF_MS", 400)
# Wilson lower-bound confidence. 1.0, not the textbook 1.96: at 1.96 a
# single successful probe scores 0.21 and would rank below a proxy nobody
# has ever probed, which is perverse. At 1.0 it scores ~0.50 -- "one probe
# is barely more than none", which is the truth.
PROXY_SCORE_CONFIDENCE_Z = float(env("PROXY_SCORE_CONFIDENCE_Z", "1.0"))
# The score for a proxy with no probes in the window. Mid-scale on purpose:
# below anything proven good, above anything proven bad. Zero would make a
# fresh CSV import unselectable. Mirrors proxies.models.UNPROBED_QUALITY_SCORE,
# which is the model field default (a default has to be a literal).
PROXY_UNPROBED_SCORE = float(env("PROXY_UNPROBED_SCORE", "50.0"))

# --- Admin-triggered probe runs --------------------------------------------
# The admin endpoint probes synchronously in the request worker, so the id
# list is capped far below MAX_IDS. 50 proxies x a 10 s timeout is already
# a worker held for most of a minute in the worst case.
PROXY_PROBE_API_MAX_IDS = env_int("PROXY_PROBE_API_MAX_IDS", 50)


# ---------------------------------------------------------------------------
# Admin audit log (P9).
#
# D12 dropped `django.contrib.admin.LogEntry` along with Django Admin and
# replaced it with log records only -- readable by any staff account,
# deletable by a superuser, and gone entirely if nobody ships them
# off-host. `adminapi.models.AdminAuditLog` is the durable replacement.
#
# Retention here is an order of magnitude longer than the probe retention
# (30 days) and that gap is the point. Probe rows are telemetry: the newest
# ones answer every question worth asking and the table grows by millions a
# day. Audit rows are evidence: they grow by hundreds a day, and the
# questions they answer -- "when did this account first start revealing
# credentials", "who changed the free plan's tier in March" -- are asked
# precisely about the distant past, usually after an incident makes someone
# look. A year is the minimum that survives an annual review cycle.
# ---------------------------------------------------------------------------

# What `prune_audit_log` deletes past, by default.
ADMIN_AUDIT_RETENTION_DAYS = env_int("ADMIN_AUDIT_RETENTION_DAYS", 365)

# The floor it refuses to go below, whatever is passed on the command
# line. Pruning audit history has to be deliberate and hard: a typo in a
# cron entry must not be able to erase the trail. Overriding this is an
# environment change, visible in config review, rather than a flag.
ADMIN_AUDIT_MIN_RETENTION_DAYS = env_int("ADMIN_AUDIT_MIN_RETENTION_DAYS", 90)

# Kill switch for the durable write. The structured log line is emitted
# either way. Here for one case: a deployment whose database is read-only
# or wedged, where losing the table write is better than losing the
# request. Leave it on.
ADMIN_AUDIT_PERSIST = env_bool("ADMIN_AUDIT_PERSIST", True)


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
        # These are now ALSO rows in the append-only `AdminAuditLog` table
        # (P9), so losing this stream no longer loses the trail. The
        # stream is still worth shipping: it is the copy an attacker who
        # owns this host and its database cannot reach. The table is the
        # copy that survives a handler nobody configured. Neither
        # substitutes for the other.
        "proxycrack.audit": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}
