# Proxy Crack — Architecture

**Package:** `com.nenobot.proxy_crack`
**Decided:** 2026-10-15

> **Android is the foundation, not the end state.** Windows, Linux and macOS clients are built
> from this same codebase later. Every decision below is made with four target platforms in
> mind — see §2.5.

Three components:

| Component | Path | Stack |
|---|---|---|
| Mobile app | `app/` | Flutter 3.47 (UI) + Kotlin (VPN engine) |
| Backend API | `backend/` | Django 5 + DRF |
| Admin panel | `backend/` | Django Admin |

---

## 1. The hard part — read this first

You chose **system-wide routing**, so the app must capture traffic from *every* app on the
device. On Android there is exactly one supported way to do that without root:

```
  All apps on device
        |
        v
  [ VpnService ]            Android API. Gives you a TUN fd.
        |                   You receive raw IP packets.
        v
  [ userspace TCP/IP ]      Must reassemble IP packets into TCP/UDP streams.
        |
        v
  [ proxy chain engine ]    SS -> SOCKS5 -> HTTP -> target
        |
        v
     internet
```

**`VpnService` hands you raw IP packets, not sockets.** Something has to implement a userspace
TCP/IP stack to turn those packets into connections a proxy can carry. That component is
conventionally called *tun2socks*. It is the largest piece of work in the project, and it is
not something to write yourself.

### Do not build the engine from scratch

A correct userspace TCP/IP stack plus multi-protocol chaining is **months** of work and a deep
source of subtle bugs (MTU, fragmentation, half-open connections, UDP associate, DNS leaks).
Mature, battle-tested engines already do exactly this.

**DECIDED (P1 resolved, 2026-10-15): embed [Xray-core](https://github.com/XTLS/Xray-core)
(MPL-2.0) via [libXray](https://github.com/XTLS/libXray) (MIT).**
Full analysis in [engine-decision.md](engine-decision.md).

| | Why |
|---|---|
| **Licence** | MPL-2.0 §3.3 explicitly permits a proprietary "Larger Work". Your Dart/Kotlin/Swift carry **zero** disclosure obligation. GPLv3 §5(c) would require licensing the *entire* APK/installer under GPL — across four products, forever. |
| **Resale risk** | GPLv3 §10 forbids "further restrictions", so any single subscriber could legally strip the paywall and republish. For a subscription product that is not an inconvenience, it is a different company. |
| Protocols | Shadowsocks, SOCKS4/4a/5, HTTP — exactly the three required |
| Chaining | `streamSettings.sockopt.dialerProxy` |
| tun2socks | Built in — `proxy/tun/` covers Android, Linux, Windows, Darwin |
| Android | libXray is MIT, so a private fork is free (needed — see §1.1) |

> **sing-box was rejected on licence, not capability.** It is GPLv3. The cross-platform scope
> (D8) made that decisively worse, not better: desktop ships outside an app store, so the one
> genuinely contested part of the GPL/app-store argument falls away and §6 applies undiluted.
> MPL also keeps the Mac App Store and a future iOS build open, which GPLv3 would close
> permanently.

### Chain semantics — ⚠️ corrected 2026-10-15

An earlier draft of this document described Xray chaining via `proxySettings`. **That is wrong
and would not start the engine.** Verified against `infra/conf/xray.go` on `main`:

```go
return nil, errors.PrintRemovedFeatureError(`outbound "proxySettings"`, `"streamSettings.sockopt.dialerProxy"`)
```

`proxySettings` exists solely to raise that error. The correct form is `dialerProxy`, **and the
config order reverses** — the user's *last* hop is the *first* outbound, because each outbound
dials *through* the one its `dialerProxy` names.

User-facing chain: `client → hop1 (SS) → hop2 (SOCKS5) → hop3 (HTTP) → target`

```json
{"outbounds": [
  { "tag": "hop3-http", "protocol": "http",
    "settings": { "servers": [{ "address": "...", "port": 8080 }] },
    "streamSettings": { "sockopt": { "dialerProxy": "hop2-socks" } } },

  { "tag": "hop2-socks", "protocol": "socks",
    "settings": { "servers": [{ "address": "...", "port": 1080 }] },
    "streamSettings": { "sockopt": { "dialerProxy": "hop1-ss" } } },

  { "tag": "hop1-ss", "protocol": "shadowsocks",
    "settings": { "servers": [{ "address": "...", "port": 8388,
                                "method": "...", "password": "..." }] } }
]}
```

The first outbound is the one that reaches the target; it dials through `hop2`, which dials
through `hop1`. **The config generator must emit hops in reverse order.** Getting this backwards
produces a chain that silently runs in the wrong direction.

### ⚠️ An HTTP hop cannot carry UDP — product-visible

Xray's docs are explicit: `http` proxies TCP only; UDP-based protocols cannot pass through.
Shadowsocks and SOCKS5 both carry UDP; HTTP does not.

**Consequence:** any chain containing an HTTP hop is **TCP-only**. No QUIC, WebRTC, VoIP, or
most games. This is worse on desktop, where browsers prefer QUIC and will stall before falling
back.

This is not a footnote — it changes the data model and the UI:
- `Proxy` needs a `supports_udp` field
- every chain needs a computed `supportsUdp` property
- the chain builder must **warn** when adding an HTTP hop breaks UDP
- the engine config needs a conditional UDP-blackhole rule so UDP fails fast instead of hanging
- entitlement/validation should enforce it server-side

**Two realities to design around:**

- **Latency compounds.** Each hop adds a full RTT. A 3-hop chain is routinely 400-900 ms.
  Users read that as "the app is slow" unless the UI sets expectations.
- **Reliability compounds.** The chain is only as live as its weakest hop. Without per-hop
  health checks and automatic failover, support tickets will bury you.

---

## 2. Mobile app

```
app/
  lib/
    core/routing/        <- ENGINE INTERFACE. Keep this abstract.
    features/
      proxies/           browse, select, build chains
      subscription/      plans, paywall, free tier
      connection/        connect/disconnect, live stats
    data/                API client, local cache
  android/app/src/main/kotlin/
    VpnConnectionService.kt   <- extends VpnService
    SingBoxBridge.kt          <- gomobile -> engine
```

Keep every engine call behind `core/routing/`. If the licence forces a swap from sing-box to
Xray, that becomes a one-file change instead of a rewrite.

### Android requirements you cannot skip

- `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_SPECIAL_USE` (Android 14+) with a justification
- A **persistent notification** while connected — mandatory, not optional
- `VpnService.prepare()` consent dialog on first connect
- `BIND_VPN_SERVICE` permission on the service
- **Always-on VPN** and **kill switch** support — users of this category expect them
- **DNS must route through the tunnel**, or you leak queries and defeat the product

---

## 2.5 Cross-platform roadmap — Windows / Linux / macOS

Android ships first. The desktop clients reuse this foundation. What carries over, and what
has to be rebuilt per platform:

```
            ┌─────────────────────────────────────────┐
            │  Flutter UI (Dart)                      │  ✅ SHARED — one codebase
            │  screens, theme, state, API client      │     adaptive layouts
            └───────────────────┬─────────────────────┘
                                │
            ┌───────────────────▼─────────────────────┐
            │  core/routing/  ProxyEngine interface   │  ✅ SHARED — one contract
            └───────────────────┬─────────────────────┘
                                │
       ┌────────────┬───────────┼───────────┬────────────┐
       ▼            ▼           ▼           ▼            ▼
   Android       Windows      Linux       macOS       (future)
   VpnService    Wintun/WFP   TUN/TAP     NetworkExt   ❌ PER-PLATFORM
   gomobile      + Admin      + CAP_NET   or helper       the real cost
   .aar          elevation    _ADMIN      + entitlement
       └────────────┴───────────┼───────────┴────────────┘
                                ▼
            ┌─────────────────────────────────────────┐
            │  sing-box / Xray core (Go)              │  ✅ SHARED — same engine
            │  protocols + chaining, all 4 platforms  │     binary vs library
            └─────────────────────────────────────────┘
```

**The engine core is portable; the privilege layer is not.** Both candidate engines are Go and
build for all four targets — that is now a primary selection criterion, not a nice-to-have.
What differs is how each OS grants you a TUN interface and what privileges that demands.

| Platform | TUN mechanism | Privilege needed | Notes |
|---|---|---|---|
| **Android** | `VpnService` | User consent dialog | No root. Engine embedded as gomobile `.aar`. |
| **Windows** | Wintun driver, or WFP | **Administrator** + driver install | Usually an elevated helper service. Driver signing matters. |
| **Linux** | TUN/TAP | `CAP_NET_ADMIN` or root | systemd service. A classic proxychains `LD_PRELOAD` mode is a lighter per-process alternative worth offering. |
| **macOS** | NetworkExtension, or `utun` + helper | **Apple entitlement**, or root helper | Needs a Mac to build. Notarisation required. |

### Design rules this imposes now

1. **Flutter layouts must be adaptive.** Bottom navigation below ~600dp; `NavigationRail` plus a
   two-pane layout above. No fixed pixel widths, no portrait-only assumptions. Retrofitting this
   is far more expensive than building it in.
2. **`ProxyEngine` must stay platform-neutral.** Four implementations will satisfy it. No
   Android vocabulary or types in the interface.
3. **Licence exposure multiplies.** GPLv3 across four commercially distributed products, with
   desktop distributed *outside* an app store — a different conveying analysis from Play Store
   distribution. This raises the stakes on the sing-box vs Xray decision considerably.
4. **Build tooling gaps exist.** Flutter Windows desktop needs **Visual Studio with the "Desktop
   development with C++" workload** (deliberately skipped during setup — see
   `Pre-Flight/INSTALL-LOG.md`). Linux desktop needs GTK dev libraries. macOS needs a Mac.

### Unaffected

The **backend is platform-agnostic** — one API serves all four clients. `Plan.max_devices` and
per-device registration become more meaningful once a user can run phone + laptop simultaneously.

---

## 3. Backend — Django

### Models

```
User            (Django auth, extended)
Plan            name, price, duration_days, max_chain_length, max_devices,
                bandwidth_limit, is_free_tier
Subscription    user -> plan, started_at, expires_at, status, platform_txn_id
Proxy           protocol (ss|socks5|http), host, port, username, password,
                country, city, is_alive, last_checked, latency_ms, tier
ProxyChain      user -> ordered list of Proxy (saved presets)
UploadBatch     csv file, uploaded_by, row_count, success/fail counts
Content         announcements, server notices, onboarding copy
UsageLog        user, bytes_up, bytes_down, session_start/end
```

### Django Admin covers the requirements directly

- **Users** — built in
- **Subscriptions / free tier** — `ModelAdmin` + list filters
- **Proxy list upload** — custom admin view accepting CSV, bulk `Proxy` creation
- **Content** — plain CRUD
- **Health dashboard** — admin view over `is_alive` / `latency_ms`

### API (DRF + JWT)

```
POST /api/auth/register           /api/auth/login        /api/auth/refresh
GET  /api/proxies/                 filter by country, protocol, tier
GET  /api/chains/   POST /api/chains/      user presets
GET  /api/subscription/            current plan + entitlements
GET  /api/content/announcements/
POST /api/usage/                   report session stats
```

**Entitlement must be enforced server-side.** Free-tier limits (chain length, proxy tier,
bandwidth) are returned by `/api/subscription/` and re-checked on every `/api/proxies/` call.
A client-side-only check gets bypassed within a week of launch.

### Never ship proxy credentials in bulk

Return proxies **scoped to entitlement**, paginated, and rate-limited. Your paid proxy
credentials are the product — an API that hands the full list to any registered account will
be scraped and resold.

---

## 4. Monetization

- **Google Play Billing** is mandatory for digital subscriptions sold in-app. Taking card
  payments directly for in-app features violates policy and gets apps removed.
- Validate purchases **server-side** against the Play Developer API. Never trust the client.
- Free tier: enforce via `Plan.is_free_tier` plus server-side entitlement.

---

## 5. Build order

| Phase | Deliverable |
|---|---|
| **1** | Django models, admin, CSV proxy upload, JWT auth, entitlement API |
| **2** | Flutter UI — auth, proxy browser, chain builder, paywall (mock engine) |
| **3** | Kotlin `VpnService` + sing-box AAR, single hop working |
| **4** | Chaining via `detour`, health checks, failover |
| **5** | Play Billing, server-side receipt validation |
| **6** | Play Console VPN declaration, privacy policy, closed testing |

Phase 3 is the risk. Everything before it is conventional work.

---

## 6. Open risks

| Risk | Note |
|---|---|
| **sing-box GPLv3** | Forces source disclosure for a closed-source paid app. Decide now. Xray (MPL) is the escape hatch. |
| **Play Store name** | "Proxy Crack" reads as piracy tooling and risks rejection. `applicationId` is permanent; the *display name* is not. Pick a different store name. |
| **VPN policy review** | Play requires a VPN declaration, a privacy policy, and review. Budget weeks, not days. |
| **iOS** | NetworkExtension needs a special Apple entitlement plus a Mac to build. Not reachable from this machine. |
| **Proxy provider ToS** | Confirm your paid provider permits redistribution via an app. Many prohibit it outright. |
| **Chain latency** | 3 hops is routinely 400-900 ms. Set expectations in the UI. |
