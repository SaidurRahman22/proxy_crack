# Pointing the app at the API

The Flutter client talks to the Django API at `/api/`. Where that API lives is
a **compile-time** setting, passed with `--dart-define`.

```bash
flutter run --dart-define=PROXY_CRACK_API_BASE_URL=http://10.0.2.2:8000
```

| Define | Default | What it does |
|---|---|---|
| `PROXY_CRACK_API_BASE_URL` | `http://10.0.2.2:8000` on Android, `http://127.0.0.1:8000` elsewhere | Origin only, no trailing slash, no `/api` suffix |
| `PROXY_CRACK_USE_MOCK_API` | `false` | `true` runs against `MockCatalogRepository` — no socket, no keystore |

---

## ⚠️ From the emulator, `127.0.0.1` is the emulator

This is the single most expensive mistake available here, because it fails as
a plain connection refused that looks exactly like "the backend is down".

| Running on | Address of the dev machine |
|---|---|
| Android emulator (AVD / QEMU) | **`10.0.2.2`** |
| Genymotion | `10.0.3.2` |
| Physical device over Wi-Fi | the machine's LAN address, e.g. `192.168.1.20` |
| Flutter desktop / `flutter test` | `127.0.0.1` |

`10.0.2.2` is the Android default in [`lib/data/api/api_config.dart`](lib/data/api/api_config.dart),
so the common case needs no define at all. When it does go wrong, the app's
offline state names the URL it tried, which is the whole reason it does.

---

## Starting the backend so the emulator can reach it

Two things have to be right, and only one of them is obvious.

```bash
cd D:\project\Proxy\backend

# 1. Bind to all interfaces, not just loopback.
# 2. ALLOWED_HOSTS must contain 10.0.2.2, or Django rejects the request with
#    a 400 DisallowedHost BEFORE it reaches a view — the app sees an HTML
#    error page, not JSON.
DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,[::1],10.0.2.2' \
  ./.venv/Scripts/python.exe manage.py runserver 0.0.0.0:8000 --noreload
```

`backend/.env` ships `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]`, which
does **not** include `10.0.2.2`. `load_dotenv` does not override an existing
environment variable, so exporting it on the command line as above wins
without editing the file. Adding `10.0.2.2` to `.env` permanently would be a
reasonable backend change; it is deliberately not made here.

### Physical device

```bash
# on the dev machine
DJANGO_ALLOWED_HOSTS='...,192.168.1.20' python manage.py runserver 0.0.0.0:8000

# build with the LAN address
flutter run --dart-define=PROXY_CRACK_API_BASE_URL=http://192.168.1.20:8000
```

The device also needs that address in
[`android/app/src/main/res/xml/network_security_config.xml`](android/app/src/main/res/xml/network_security_config.xml),
which permits cleartext **only** for `10.0.2.2`, `10.0.3.2`, `localhost` and
`127.0.0.1`. Add a LAN address there temporarily and do not commit it. The
blunt alternative — `android:usesCleartextTraffic="true"` on `<application>` —
would disable the protection for every host the app ever talks to, including
a production API, and is the wrong trade for this product.

Alternatively, skip cleartext entirely with `adb reverse`:

```bash
adb reverse tcp:8000 tcp:8000
flutter run --dart-define=PROXY_CRACK_API_BASE_URL=http://localhost:8000
```

---

## P26 — the API host is resolved before the tunnel, and pinned

### The defect

The `process` routing rule in
[`lib/core/routing/xray_config_builder.dart`](lib/core/routing/xray_config_builder.dart)
keeps this app's **own sockets** out of the chain by matching our UID, so a
broken chain cannot cut the app off from its own backend. It cannot do the
same for our **name lookups**.

On API 29+ an app's `getaddrinfo` does not leave the app. It is handed to
**`netd`, which runs as root (UID 0) in a separate process**. The DNS packets
that reach the tunnel therefore carry *netd's* UID, not ours, and a rule
written against our UID can never match them.

In production, where the base URL is a hostname rather than today's bare
`10.0.2.2`, that reopens the deadlock the pre-flight walk and the
unconditional off switch were built to close:

1. the chain breaks;
2. the app tries to reach the API to re-resolve or re-fetch credentials;
3. the lookup for the API hostname goes into the broken chain and never
   answers;
4. the app cannot recover.

Dev never shows it, because a bare IP is never looked up.

### ⛔ Why netd's UID is **not** exempted

Adding netd's UID to the `process` rule is the obvious-looking fix and it is
the wrong one. `netd` resolves for **every app on the device**, so exempting
it takes the whole device's DNS off the tunnel — every hostname every app
visits goes to the user's ISP in clear. That is a device-wide DNS leak, and
preventing exactly that is what this product is for. The rule stays keyed to
our own UID.

### The mitigation

Resolve the API host **before `VpnService.establish()`**, in the same
pre-flight phase that walks the chain, while the device still has ordinary
connectivity — and dial that address for the rest of the session. There is
then no name to look up while the tunnel is up, so whose UID a lookup would
have carried stops mattering.

| | |
|---|---|
| Where | [`lib/core/net/host_pin.dart`](lib/core/net/host_pin.dart), used by `AndroidVpnEngine` step 3 and by [`lib/data/api/pinned_http_client.dart`](lib/data/api/pinned_http_client.dart) |
| When | Once per connect, before the TUN. Never re-resolved mid-session — that would reintroduce the dependency at the worst moment. |
| Scope | One host: ours. It is not a DNS cache and must not become one. |
| Base URL already an IP | **No-op.** Nothing is looked up and nothing is pinned. |
| Cannot resolve | **The connect is refused**, before the TUN. A connect that does not happen is recoverable; a device that cannot ask for the tunnel to stop is not. |
| Several A records | IPv4 first, then the numerically lowest address — so the pin is the same on every connect from the same answer. |
| IPv6 | Taken **only** when there is no IPv4 at all (an IPv6-only carrier with DNS64 returns nothing else). Never silently preferred. |
| Released | When the engine reports `disconnected` — **not** on `error`, because the service publishes an error while still holding the interface closed, and that is when the pin matters most. |

The pre-flight walk gets the same treatment for **hop 1** only: it is dialled
by address so a hostname hop cannot be looked up into a chain that a previous
session left up. Hops 2..n stay as names on purpose — they are resolved by
the *previous hop*, never by this device.

### ⚠️ TLS: the request URL is never rewritten

Swapping the hostname out of the URL for the address is the naive version and
it breaks TLS, in production only:

* the certificate is issued for the name, so validating it against an address
  fails unless the certificate carries an IP SAN, which public CAs rarely
  issue;
* **SNI** would carry the address, so any host serving several names from one
  address answers with the wrong certificate;
* the **`Host` header** would carry the address, which Django rejects with a
  400 `DisallowedHost` before a view runs.

So the URL is left exactly as it is, and only the TCP destination changes,
through `HttpClient.connectionFactory`:

| carries | value |
|---|---|
| TCP destination | the pinned address |
| TLS SNI | the hostname |
| certificate validation | the hostname |
| `Host` header | the hostname |

One trap is worth repeating from
[`pinned_http_client.dart`](lib/data/api/pinned_http_client.dart): when
`connectionFactory` is set, **dart:io uses the returned socket as-is** and
does not secure it. A factory that hands back a plain socket for an `https`
URL speaks cleartext at a TLS port. The factory calls
`SecureSocket.secure(..., host: url.host)` itself, with no `onBadCertificate`
and no custom `SecurityContext`, so validation is exactly as strict as it was
before the pin existed.

`test/host_pin_test.dart` pins this by reading the TLS ClientHello off a plain
socket and asserting the `server_name` extension carries the hostname and not
the address.

---

## Running without a backend

```bash
flutter run --dart-define=PROXY_CRACK_USE_MOCK_API=true
```

`MockCatalogRepository` answers from `lib/data/mock/mock_data.dart` behind the
same `CatalogRepository` interface, and the session is auto-signed-in. Widget
tests take this path automatically by passing `repositoryOverride`, which also
avoids the platform keystore — `flutter_secure_storage` has no plugin
registrar in a test binary.

---

## Token custody, in one paragraph

The **access token lives in memory only** and is gone on every cold start;
the app mints a new one from the refresh token on its first request, which is
why the server log shows a `401` on `/api/subscription/` followed by a
`POST /api/auth/refresh/` and a retry every time the app launches. The
**refresh token lives in `flutter_secure_storage`** (Android Keystore), never
in `SharedPreferences` — whoever holds it *is* the account for thirty days.
The backend **rotates refresh tokens and blacklists the old one**, so both
halves of the refresh response are stored; keeping only `access` signs the
user out at the *next* refresh, half an hour later, on a different screen.
See [`lib/data/api/transport.dart`](lib/data/api/transport.dart).

---

## Creating a test account

The dev database holds **one** real proxy, and it is `premium`. The free plan
caps `max_proxy_tier` at `free`, so a free-tier account sees an **empty**
proxy list — correct behaviour, confusing during a demo. Give the test user a
Pro subscription:

```python
# backend/.venv/Scripts/python.exe manage.py shell
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from billing.models import Plan, Subscription, SubscriptionStatus

U = get_user_model()
user = U.objects.create_user(email='mobile@proxycrack.local', password='<see ACCESS.md>')
Subscription.objects.create(
    user=user, plan=Plan.objects.get(code='pro-monthly'),
    started_at=timezone.now(), expires_at=timezone.now() + timedelta(days=30),
    status=SubscriptionStatus.ACTIVE, platform='manual',
)
```

---

## Throttles worth knowing before you test

The backend's scopes are deliberately tight. Hitting one is a normal
operational event, not a fault, and the app renders it as "Rate limit
reached" with the server's own wait rather than a generic error.

| Scope | Budget | Notes |
|---|---|---|
| `proxy_credentials` | **60/hour** | `GET /api/proxies/{id}/`. One call per hop per connect (D18), each audit-logged. A 3-hop connect costs 3. |
| `chain_resolve` | 120/hour | Every connect and every failover. |
| `chain_diversity` | 240/hour | Fetched on a chain edit, debounced 400 ms — never on a screen build. |
| `chain_write` | 60/hour | `POST`/`PATCH /api/chains/`. |
| `login` | 20/hour | Easy to exhaust while testing sign-in. |
| `content` | 60/hour | Fetched once per app start. |
| `usage_report` | 120/hour | Fired once per session, on disconnect. |

Throttle state lives in `LocMemCache` and is **per process**, so restarting
`runserver` clears it.
