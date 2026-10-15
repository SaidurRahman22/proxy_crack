# Proxy Crack — Project Memory

Single source of truth for decisions, state, and context. Update this whenever a decision is
made or reversed. Anything agreed in conversation but not written here is considered lost.

**Last updated:** 2026-10-15

---

## 1. Identity

| Field | Value | Mutable? |
|---|---|---|
| Product name | Proxy Crack | yes |
| Org | Neno-Bot | yes |
| Package / `applicationId` | `com.nenobot.proxy_crack` | ❌ **permanent after Play Store publish** |
| Flutter project name | `proxy_crack` | hard to change |
| Repo root | `D:\project\Proxy` | yes |

> `Neno-Bot` could not be used literally — Android `applicationId` and Java package segments
> reject hyphens. Normalised to `com.nenobot`.

---

## 2. Product definition

A mobile app that routes device traffic through a **user-defined chain of proxies**, the way
Linux `proxychains` does. Proxies are sourced from the operator's paid providers and served to
users according to subscription tier. An admin panel manages users, subscriptions, free tier,
content, and bulk proxy-list upload.

---

## 3. Decisions made

| # | Date | Decision | Rationale | Reversible? |
|---|---|---|---|---|
| D1 | 2026-10-15 | **Flutter** for the mobile app | Cross-platform, already installed and verified | moderate |
| D2 | 2026-10-15 | **System-wide VPN routing** (not in-app only) | User requirement: behave like proxychains for all apps | ❌ defines the whole architecture |
| D3 | 2026-10-15 | **Django + Django Admin** backend | Admin panel is near-free; Python already installed | moderate |
| D4 | 2026-10-15 | **SOCKS5 + HTTP + Shadowsocks** protocols | Covers commercial providers plus an encrypted first hop | yes |
| D5 | 2026-10-15 | Toolchains live in `D:\dev\` | No spaces in path — avoids Gradle/CocoaPods failures | yes |
| D6 | 2026-10-15 | Monorepo: `app/` `backend/` `Project/` `Pre-Flight/` | Three components, one history | yes |
| D7 | 2026-10-15 | **Admin panel stays minimal — stock Django Admin only** | Operator will build a custom panel later. Do not invest in admin UX now; keep models clean and extensible so a custom panel can be layered on. | yes |
| D8 | 2026-10-15 | **Android is the FOUNDATION, not the end state. Windows, Linux and macOS clients follow.** | The product is a cross-platform proxychains. Mobile ships first and proves the model; desktop is built from the same Flutter codebase and the same engine core. | ❌ shapes every layer |
| D9 | 2026-10-15 | **Engine = Xray-core (MPL-2.0) via libXray (MIT). sing-box REJECTED.** | **Licence, not capability.** MPL §3.3 permits a proprietary "Larger Work" — Dart/Kotlin/Swift carry zero disclosure obligation. GPLv3 §5(c) would put the entire APK/installer under GPL across four products, and §10 would let any single subscriber legally strip the paywall and republish. D8 made this decisively worse: desktop ships outside an app store, so the contested part of the GPL/app-store argument falls away and §6 applies undiluted. Xray's `proxy/tun/` also removed sing-box's last technical edge. | ❌ hard to reverse after Phase 3 |
| D10 | 2026-10-15 | **Platform order: Android → Linux → Windows → macOS** | Linux second, not Windows: cheapest way to prove the separate-process engine model with no driver, no signing cert, no AV politics, no new hardware. Relative cost Android 1x · Linux 1.5x · Windows 2x · macOS 4x. | yes |
| D11 | 2026-10-15 | **Proxy credentials encrypted at rest (Fernet, rotating keys, fail-loud)** | The paid credentials *are* the sellable asset. API-surface controls do not protect a DB dump, backup, or read replica. App refuses to start without a valid key rather than silently writing plaintext. | yes |

### D8 consequences — read before designing anything

| Layer | Impact |
|---|---|
| **Flutter UI** | Must be **adaptive**, not phone-hardcoded. Bottom nav below ~600dp, NavigationRail + two-pane above. No fixed pixel widths, no portrait assumption. |
| **Engine core** | sing-box and Xray are both **Go** — the same core already runs on all four platforms. This is now a primary selection criterion, not a bonus. |
| **TUN / privilege layer** | **Differs per OS and is the real porting cost.** Android `VpnService`; Windows Wintun/WFP + Administrator; Linux TUN/TAP + `CAP_NET_ADMIN`; macOS NetworkExtension entitlement or privileged helper. |
| **`core/routing/` interface** | Will have **four** implementations. Keep it free of Android-specific vocabulary. |
| **Licence (P1)** | GPLv3 exposure now spans **four commercially distributed products**. Desktop distribution outside an app store changes the GPLv3 conveying analysis vs Play Store. Raises the stakes on P1 considerably. |
| **Backend** | Unaffected — one API serves all platforms. `Plan.max_devices` and device registration become more meaningful. |
| **Build tooling** | Flutter **Windows desktop requires Visual Studio + "Desktop development with C++"**, which was deliberately skipped during setup. Will be needed before the Windows target. Linux desktop needs GTK dev libs. macOS needs a Mac. |

### Pending decisions — blocking

| # | Question | Why it matters | Blocks |
|---|---|---|---|
| ~~P1~~ | ~~sing-box vs Xray-core~~ | **RESOLVED → D9. Xray-core via libXray.** | — |
| P2 | Play Store **display name** | "Proxy Crack" reads as piracy tooling; rejection risk. `AndroidManifest` still says `proxy_crack`. | Phase 6, but pick early |
| P3 | `git config user.name` | Commits currently have no author name | **every commit — nothing is committed yet** |
| P4 | Paid proxy provider + their ToS | Many providers forbid redistribution via an app | Phase 1 data model |
| P5 | **Wintun redistribution consent** | Xray bundles `wintun.dll` under WireGuard LLC's Prebuilt Binaries Licence, which bars redistribution without written consent except via a "Permitted API" carve-out. Runtime loading *appears* to qualify — needs a lawyer. Wintun is **mandatory** for Windows TUN. Engine-neutral (sing-box uses it too). **Long lead time.** | Windows release |
| P6 | **Desktop payment rail** | Play Billing is mandatory for Android *only*. Desktop needs Stripe/Paddle or similar, and one subscription must unlock all four clients. | Phase 5 |
| P7 | **UDP strategy for HTTP hops** | An HTTP hop cannot carry UDP (TCP only). Any chain containing one is TCP-only — no QUIC, WebRTC, VoIP, games. Needs `Proxy.supports_udp`, a chain-level `supportsUdp`, a UI warning, a UDP-blackhole rule, and server-side validation. Worse on desktop where browsers prefer QUIC. | Phase 2 data model |
| P8 | **Credential key custody** | Keys live in the process environment. Defends dumps/backups/replicas but not code execution on the app host. Move to KMS before real paid credentials land in production. | before production |

---

## 4. Environment state — verified 2026-10-15

| Component | Version | Location |
|---|---|---|
| JDK | Temurin 17.0.20.1 | `D:\dev\jdk-17` |
| Android SDK | platform 36, build-tools 36.0.0, NDK 28.2.13676358 | `D:\dev\android-sdk` |
| adb / emulator | 37.0.1 / 37.2.12 | on PATH |
| Flutter | 3.47.7 stable (Dart 3.13.5) | `D:\dev\flutter` |
| AVD | `Pixel8_API36` (Android 16, x86_64, WHPX) | `~\.android\avd` |
| Python | 3.11.15 | system |
| Django | 5.2.18 + DRF 3.18.3 + SimpleJWT 5.5.1 | `backend/.venv` |
| Node | 24.18.0 | system |

**Verified end-to-end:** Flutter hello-world built (144 MB APK, 139 s), installed via adb, and
rendered on `emulator-5554`.

See `Pre-Flight/INSTALL-LOG.md` for the full setup record and the gotchas hit.

---

## 5. Hard-won gotchas — do not rediscover these

1. **`sdkmanager "platforms;android-36"` installs nothing and still exits 0.** `cmd.exe` splits
   arguments on `;`. Use `/`: `platforms/android-36`. Verify on disk, never trust exit code.
2. **Gradle fails on missing NDK.** Flutter pins `flutter.ndkVersion`; Gradle cannot auto-install
   it. Install `ndk/28.2.13676358` explicitly.
3. **Oracle Java 23 outranks JDK 17 on PATH** (Machine PATH beats User PATH). Harmless —
   Gradle and Flutter read `JAVA_HOME`. `java -version` reporting 23 is cosmetic.
4. **Never launch the emulator from a background task** — it dies with its parent. Use
   `Start-Process` or the desktop shortcut.
5. **New terminals required** after env var changes. Old VS Code windows lack the new PATH.
6. `sdkmanager --licenses` is now a no-op; licences are accepted at install time.

---

## 6. Constraints

| Constraint | Consequence |
|---|---|
| **Windows dev machine** | iOS cannot be built here. Needs a Mac, cloud Mac, or CI. |
| **No admin rights in session** | All tooling installed user-scope. Defender exclusions still pending. |
| **Google Play Billing mandatory** | Direct card payments for in-app features violate policy. |
| **Play VPN declaration required** | Privacy policy + review. Weeks, not days. |
| **Chain latency compounds** | 3 hops ≈ 400-900 ms. UI must set expectations. |
| **Entitlement must be server-side** | Client-only checks get bypassed quickly. |

---

## 7. Session log

### 2026-10-15
- Built `Pre-Flight/` — 12-file setup checklist covering Flutter, Kotlin, Swift, RN, MAUI
- Executed setup: JDK 17, Android SDK + NDK, Flutter 3.47.7, AVD, VS Code extensions
- Hit and documented 6 gotchas (see §5)
- Verified full chain: build → install → launch → render on emulator
- Scaffolded `app/` Flutter project, `applicationId = com.nenobot.proxy_crack`
- `git init` at repo root, `.gitignore` covering Flutter + Django + secrets. **Not yet committed** (P3)
- Architecture decided (D1-D6); `Project/architecture.md` written
- Django 5.2.18 + DRF installed in `backend/.venv`
- Admin scope reduced to stock Django Admin (D7) — custom panel deferred to operator
- Design system generated via ui-ux-pro-max → `design-system/proxy-crack/MASTER.md`
  - Dark-first: bg `#0F172A`, card `#192134`, accent `#22C55E`, destructive `#DC2626`, font Inter
  - Style: Minimalism / Swiss. Palette note: "Shield dark + connected green"
  - ⚠️ The tool also emitted a *landing page* section pattern (hero/proof/CTA). Deliberately
    discarded — this is an app shell, not a marketing site. Only colour/type/motion tokens used.
- **Three parallel workstreams launched:**
  | Stream | Scope | Owns |
  |---|---|---|
  | Backend | Phase 1: models, stock admin, CSV upload, JWT, entitlement API | `backend/` |
  | Engine decision | Resolve P1 (sing-box GPLv3 vs Xray MPL) + integration plan | `Project/engine-decision.md` |
  | Flutter UI | Theme, engine interface + mock, 4 screens | `app/` |

### Workstream boundaries
To avoid collisions: backend owns `backend/`, UI owns `app/`, engine research writes only
`Project/engine-decision.md`. No agent commits to git (P3 unresolved).
