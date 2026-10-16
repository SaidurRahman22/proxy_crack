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
| ~~D7~~ | 2026-10-15 | ~~Admin panel stays minimal — stock Django Admin only~~ | **SUPERSEDED by D12 the same day.** Operator saw the stock admin and rejected it on looks. | — |
| D12 | 2026-10-15 | **Custom React SPA admin replaces Django Admin.** Vite + React + TypeScript in `admin/`, consuming a new staff-only API at `/api/admin/`. | Stock Django Admin looks dated and the operator wants the panel to match the product. Django Admin stays mounted at `/admin/` as a superuser fallback, not deleted. | moderate — ongoing maintenance cost |

### D12 consequences

| | |
|---|---|
| **The mobile API cannot serve the admin.** | `/api/` is entitlement-scoped by design — a free user sees a filtered subset. Admin needs the opposite: unscoped, staff-only. Hence a separate `/api/admin/` surface. |
| **Security surface doubles.** | The admin API is unscoped, so a missing permission class leaks everything. Every endpoint requires `IsAuthenticated AND is_staff`; non-staff must get 403, not data. Tests parametrise over every endpoint to catch a missed one. |
| **Contract-first.** | `Project/admin-api-contract.md` is the spec both halves are built against, so backend and SPA can be developed in parallel. |
| **Credentials.** | Decrypted on the admin *detail* endpoint only, never in lists, never cached client-side, audit log still fires. |
| **Cost.** | Re-implements auth, permissions, CRUD, forms, validation and CSV upload that Django Admin provided for free. Accepted deliberately for brand control. |
| ⚠️ **AUDIT LOG REGRESSION** | Django Admin ships `django.contrib.admin.LogEntry`, which records every add/change/delete with actor, object and timestamp. **The SPA abandons it.** Nothing in the contract replaces it. An admin console holding the company's entire sellable asset would have *no* record of who changed what. A replacement audit log is required, not optional — and `Plan` entitlement changes are its single highest-value event (see P9). |

### Confirmed defects found by the threat model — 2026-10-15

| # | Severity | Finding | Status |
|---|---|---|---|
| **V1** | 🔴 **Data loss** | **CSV import silently blanks credentials.** `importers._clean_row` always writes `username`/`password`/`ss_method` into `defaults`, so a metadata-only re-upload wipes them via `update_or_create`. **Reproduced live:** `'live_user'/'live_pass'` → `''/''`. Encrypted at rest with no plaintext backup, so **irrecoverable**. | sent for fix |
| **V2** | 🔴 Critical | **Every throttle is per-process.** `CACHES` is unset → `LocMemCache` → the 60/hour credential throttle is actually N×60/hour across N workers, unshared across hosts, and resets on deploy. Confirmed: no `CACHES` key in settings. | sent for fix |
| **V3** | 🔴 Critical | **Staff self-escalation.** `is_staff`/`is_superuser` are writable CRUD fields. `PATCH {"is_superuser": true}` on self is live. Today `is_staff` ≡ `is_superuser`. | sent for fix |
| **V4** | 🔴 Critical | **`set_password` has no privilege boundary** — any staff can reset the superuser's password and log in at `/admin/`. | sent for fix |
| **V5** | 🟠 High | **Entitlement flip.** One `PATCH` to the free plan's `max_proxy_tier` re-entitles the whole free user base; credentials are then scraped via the *mobile* API with throwaway accounts. Touches no admin throttle, fires no admin audit line. Stealthier than direct exfiltration. | sent for fix |
| **V6** | 🟠 High | No CSV size/row cap on the API path; no cap on bulk `ids[]` bodies; no admin-side credential throttle. | sent for fix |
| **V7** | 🟡 Medium | `bulk_delete` cannot express partial failure; `ChainHop.proxy` is `PROTECT`, so one referenced id 500s the batch. | ✅ **FIXED** |

**All V1–V7 fixed and independently verified. 244 tests pass.** V1 verified in both
directions — a metadata-only CSV preserves credentials, *and* an explicit empty-column clear
still works (the naive fix breaks the second).

### Hardening added beyond the findings

| | |
|---|---|
| **Token separation** | Admin tokens carry a `scope: "admin"` claim checked at the *authentication* layer, before any permission class. A mobile token gets 403 `admin_scope_required`. Turns "one missing permission class = total compromise" into two independent failures. |
| **Boot refusal** | `adminapi.E001` walks the resolved URLconf including `@action` routes and **refuses to start** if any `/api/admin/` route lacks `IsStaffUser`. Structural answer to Ground Rule 2. |
| **Graded throttles** | credential reveal 120/h **and 400/day** (an hourly cap alone is defeated by patience); bulk 20/h; CSV 10/h; login per-IP 10/min + per-email 20/h on *failures only*, so an operator cannot lock themselves out. |
| **Deploy checks** | `core.E002` LocMem+throttling · `E003` placeholder SECRET_KEY · `E005` `ALLOWED_HOSTS=*` · `W001` JWT key == SECRET_KEY |
| **Last-free-plan guard** | Deactivating the last active free plan is refused (409) — otherwise every free user silently drops to `ENTITLEMENT_FALLBACK`. |

### Still open

| # | Gap |
|---|---|
| **P9** | **Durable `AdminAuditLog` table.** Audit events are structured log records only — readable by staff, deletable by a superuser. Biggest remaining gap. |
| **P10** | No MFA; 30-day refresh token in `localStorage`. |
| **P11** | CSV import is synchronous in a request worker; no out-of-band processing. |
| **P12** | CSV *export* sanitisation (formula injection) — no export endpoint exists yet; the rule belongs in the contract **before** someone adds the button. |
| ~~P13~~ | **RESOLVED 2026-10-15.** Credential disclosure moved to `POST /api/admin/proxies/{id}/reveal/`. The detail `GET` now returns the credential-free list serializer, so opening a drawer is no longer a disclosure — the audit log records disclosures rather than page views, and the 120/h + 400/day budget applies only to deliberate reveals. The SPA had independently built against `/reveal/` with a fallback, and deliberately avoided `useQuery` so plaintext never enters the query cache. 245 tests pass. |

### Contract drift — backend deviates from `admin-api-contract.md`

1. **`/auth/refresh/` returns `{access, refresh}`, not `{access}`** — rotation + blacklist are on, so a client keeping the old refresh token is logged out at its next refresh. **Relayed to the SPA agent.**
2. Login returns **403 for every failure** including wrong password, so the response cannot reveal whether an account exists.
3. Generic admin throttle set to 1200/h rather than 600/h, since high-value endpoints now carry their own tighter scopes.
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

## 6.5 Roadmap execution — started 2026-10-15

Full analysis in [roadmap.md](roadmap.md). Items are being built **sequentially**, not in
parallel: R1 and R2 both add fields to `Proxy`, so concurrent agents would collide on the
migration chain.

| # | Item | Why it beats proxychains | Status |
|---|---|---|---|
| **R1** | **Health checking, quality scoring, positional failover** | proxychains discovers a dead hop by failing on it — no probe, no failover, no quality notion | ✅ **DONE** — 438 tests (193 new), verified |
| R2 | ASN / jurisdiction / operator diversity scoring | proxychains will happily build a 3-hop chain inside one datacenter and say nothing | queued |
| R3 | Kill switch, DNS through tunnel, leak tests | proxychains leaks on failure; `proxy_dns` is a known-weak hack | blocked on Phase 3 engine |
| R4 | Latency-budgeted chain composition | proxychains makes you hand-edit a config and find out by trying | queued |
| R5 | Per-app split tunnelling | `VpnService` supports it natively; proxychains needs a shell prefix per command | blocked on Phase 3 |
| R6 | UDP support + HTTP-hop warning | proxychains is TCP-only; SOCKS5/SS carry UDP, HTTP does not (see P7) | queued |
| R7 | Chain presets by purpose | proxychains assumes you already know what you are doing | queued |
| R8 | Per-hop observability | proxychains tells you nothing when a chain is slow | partly in R1 |

### R1 design decisions (2026-10-16)

| # | Decision | Why |
|---|---|---|
| **D13** | Reliability **multiplies** the score; latency only scales it within a 40% band | Makes latency genuinely secondary. A proxy dropping a third of its connections cannot climb back by being fast. |
| **D14** | Reliability is a **Wilson lower bound**, z=1.0 (not the textbook 1.96) | Without it, 1/1 outranks 198/200. At z=1.96 a single success scores 0.21 — *below* an unprobed proxy, which is perverse. z=1.0 gives 0.50/0.91/0.98 at 1/10/50 probes. |
| **D15** | Unprobed proxy scores **50**, not 0 or 100 | 0 makes every fresh CSV import unselectable; 100 beats everything actually measured. |
| **D16** | `is_alive` is **asymmetric** — 3 consecutive failures to die, 1 success to revive | A flapping proxy is better punished by its success rate than by a boolean. Verified: score stays depressed after revival. |
| **D17** | Constrained hops resolve **most-constrained-first**, not in position order | With the no-reuse rule this is bipartite matching. Position order fails chains that are satisfiable. Heuristic; Hopcroft-Karp slots in later. |
| **D18** | `/api/chains/{id}/resolve/` returns proxy **identities + a `credentials_url` per hop**, never credentials inline | A 3-hop resolve returning credentials is three sets of paid credentials in one request, outside the 60/hour throttle and the per-disclosure audit line — exactly the bulk disclosure D11 forbids. Costs 3 extra round trips per connect. |

### ✅ P14 RESOLVED 2026-10-16 — Shadowsocks prober shipped

`proxies/shadowsocks.py` implements a real AEAD client (`aes-256-gcm`, `aes-128-gcm`,
`chacha20-ietf-poly1305`). SS proxies are probed, not skipped. **628 tests pass.**

**Error-kind mapping, including where it refuses to guess:**

| Observed | Kind | |
|---|---|---|
| TCP connect fails | `refused`/`timeout`/`dns` | distinguishable — happens before SS logic |
| Reply that does not authenticate | `auth_failed` | only a wrong key/cipher or non-SS endpoint does this |
| Decryptable reply | success | proves password, cipher **and** upstream path |
| **Silence (EOF / deadline / RST)** | **`unverifiable`** (new kind) | **genuinely indistinguishable — the result says so** |

EOF, timeout and RST deliberately collapse onto one kind: surfacing them separately would give an
operator two different-looking verdicts for one indistinguishable situation.

**Crypto vector provenance** — every vector pinned to a source outside this repo:
`EVP_BytesToKey` from the OpenSSL 3.5.7 CLI (exact command recorded); HKDF-SHA1 from RFC 5869
Appendix A TC5/TC6; AES-GCM from the GCM spec; ChaCha20-Poly1305 from RFC 8439 §2.8.2.

⚠️ **Known weakness, stated rather than hidden:** there is **no published known-answer vector for
the Shadowsocks composition** (subkey derivation + nonce sequence + chunk framing + request
header) — the spec is prose and ships none. The composition is therefore tested against a second
implementation written from the spec inside the test file, sharing no code with the production
one. **Agreement between two implementations written from the same prose is weaker evidence than
a published vector.** Recorded in the test module docstring.

**SS2022 (`2022-blake3-*`) is not implemented** — reported as `unsupported_cipher`, distinct from
a failure. An unsupported cipher is our gap, not a dead proxy.

---

<details>
<summary>Original P14 writeup (resolved — kept for context)</summary>

**SS is one of the three protocols in D4, and the prober skips it entirely.** Shadowsocks is
designed to be indistinguishable from random bytes: the server sends no greeting and no reply to
anything it cannot decrypt, and a *wrong password produces exactly the same silence as a correct
one*. There is no handshake to observe, so a real probe must be a real AEAD client.

All three shortcuts were correctly refused:
- bare TCP connect recorded as success → a green tick for a dead container
- recorded as failure → marks every working SS proxy dead and removes it from all chains
- shipping untested crypto

**Current behaviour:** SS proxies are skipped, write no probe row, and keep the unprobed score of
50. `probe_proxies` reports the skip count. Fixing it is one entry in `SocketProber.handler_for`.

</details>

### ✅ P15 RESOLVED 2026-10-16 — the diagnosis was fixed, not the semantics

Excluding unprobed proxies from a latency guarantee is still correct. What changed is that the
operator can now tell the two causes apart, because they have **opposite remedies**:

- each health stage reports `excluded_measured` vs `excluded_unprobed`
- new code `hop_unsatisfied_unprobed`, raised **only when relaxing the threshold would actually
  produce candidates** — so a chain emptied by country or entitlement is not blamed on probing
- `ChainHop.allow_unprobed` opt-in (default off) for bootstrapping a fresh inventory
- **"Unprobed" is `probe_count = 0`, not `latency_p95_ms IS NULL`.** A proxy that failed 50 probes
  also has a null p95; keying on the null would have re-admitted the worst proxies in the
  inventory under a flag promising "nothing known yet".

### ✅ P9 RESOLVED 2026-10-16 — durable audit log, append-only verified

`AdminAuditLog`, written from the existing `audit()` seam so all 15 call sites gained persistence
without re-instrumenting a single view.

**Append-only enforced at four independent layers.** I attacked it directly; all five refused:

| Attack | Result |
|---|---|
| model `.save()` on an existing row | refused |
| queryset `.update()` — never loads an instance | refused |
| model `.delete()` | refused |
| **raw SQL `UPDATE`** | **refused by database trigger** |
| **raw SQL `DELETE`** | **refused — 30-day hard floor in the migration** |

The trigger caught a real design trap: Django's `SET_NULL` on user deletion *is* an UPDATE, so a
blanket ban broke actor survival — and the obvious "fix" would have been `CASCADE`, erasing the
history of exactly the people you most need a record of. The carve-out permits only
`actor_id → NULL` with all nine other columns pinned, and a test fails if a column is added later
without updating the trigger.

Retention: 365 days (vs 30 for probes), dry-run by default, floor of 90 days, and the prune
records itself **after** deleting so the record sits outside its own blast radius.

### D19 — mobile credential endpoint also audited (agent's judgement call, accepted)

`/api/proxies/{id}/` was wired into the audit seam too. Not requested, and it touches `/api/`.
The reasoning is sound: D18 sends a client there once per hop on every connect and every
failover, so that endpoint — not the console — is where most paid credentials actually leave the
server. Auditing only the console would have covered the rare path and missed the common one.
Purely additive; the Phase 1 log line is byte-for-byte unchanged.

### Known limitations carried forward

| | |
|---|---|
| **PostgreSQL trigger reviewed but never executed** | Dev DB is SQLite, so only that branch is exercised. Needs PG 11+ for `EXECUTE FUNCTION`. Validate before the Postgres move. |
| **`TransactionTestCase` / `manage.py flush`** | Both use `DELETE FROM`, which the trigger refuses for rows under 30 days. Nothing hits it today; a future test that audits something will fail at teardown with a message naming the migration. |

### Positions taken

- **Ship the lightweight per-process mode on Linux too.** For Linux power users that is
  genuinely the right tool, and shipping it buys credibility with exactly the audience that
  knows what proxychains is. Do not force TUN on people who want a shim.
- **Never claim anonymity guarantees.** Chaining through commercial proxies bought under one
  account with one payment trail is **not** Tor. The honest description is *layered egress
  routing*. Overclaiming is a legal and reputational risk.
- **No "free unlimited" tier.** The credentials are the cost base; free must be strictly bounded
  or the unit economics invert.
- **Do not write our own TUN/IP stack** — months of subtle bugs. Xray (D9) covers it.

## 7. Session log

> **Current status lives in [STATUS.md](STATUS.md)** — read that first.

### 2026-10-16
- **R1 shipped** — health checking, quality scoring, positional failover (438 tests)
- **P14 closed** — Shadowsocks AEAD prober. SS proxies are measured, not skipped.
- **P15 closed** — unprobed vs measured exclusion distinguished; `allow_unprobed` opt-in
- **P9 closed** — durable `AdminAuditLog`, append-only verified by attacking it (5 attacks
  refused, including raw SQL). Closes the regression D12 introduced.
- **D19** — mobile credential endpoint wired into the audit seam (that is where most paid
  credentials actually leave the server, via D18's per-hop fetch)
- Backend at **628 tests**, 38 migrations, 13 models across 4 apps
- Operator committed twice (`8c68929` admin SPA, `17c728a` health + pruning). **3 commits total,
  working tree clean, nothing pushed to GitHub yet.**
- `STATUS.md` created; `graph.md` ER diagram updated with `ProxyProbe`, `AdminAuditLog` and the
  constrained-hop columns; `roadmap.md` R1 marked shipped

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
