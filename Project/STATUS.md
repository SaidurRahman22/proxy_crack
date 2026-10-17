# Proxy Crack — Current Status

> ## ⏸️ PAUSED 2026-10-17 — resume here
>
> **R9 (Webshare provider sync) is ~90% done and safely committed.** Work was paused
> deliberately, not abandoned.
>
> **State is clean:** 911 tests pass, `manage.py check` clean, no migration drift,
> migration `0006_provider_sync` applied.
>
> **Verified working:** the Webshare API connection. `GET /api/v2/proxy/list/` returns
> **HTTP 200 and all 10 proxies**, all `valid: true`, with `asn_number` / `asn_name` /
> `country_code` / `city_name` per record. Token is in `backend/.env`.
>
> **What is built but NOT yet verified against the live API:**
> `proxies/providers.py`, `management/commands/sync_webshare.py`, migration `0006`,
> admin sync endpoint, 119 new tests (all passing, all against the fake).
>
> ### To resume, in order
> 1. `.\.venv\Scripts\python.exe manage.py sync_webshare --dry-run` — confirm it sees all 10
> 2. Run it for real; confirm **proxy id 11 (`45.38.107.97`) is ADOPTED, not duplicated** —
>    it was added by hand and has no `external_id`
> 3. Confirm `IpIntelRecord` rows are populated from the provider's ASN data, marked
>    **provider-asserted** rather than independently verified
> 4. Confirm every synced proxy gets `provider = "webshare"`
> 5. **Demonstrate R2 on the real pool**: a chain with `198.23.243.226` and `198.46.161.42`
>    at entry and exit should raise a **critical** finding — both are **AS36352 (Hostpapa)**
> 6. Any chain from this pool should also raise `entry_exit_shared_provider`, because all ten
>    come from Webshare
>
> ### What the real inventory looks like (confirmed live)
> **9 distinct ASNs, 5 countries, 1 provider.** Good network spread; zero operator diversity.
> Webshare holds the payment record and knows all ten IPs are yours. This is exactly what R2
> is built to say out loud.


**Last updated:** 2026-10-16
**Read this first.** Detail lives in [architecture.md](architecture.md),
[memory.md](memory.md), [graph.md](graph.md), [roadmap.md](roadmap.md).

---

## At a glance

| | |
|---|---|
| **What works today** | Backend API + admin console, both running and verified. Mobile app UI with a mock engine. |
| **The VPN engine** | ✅ **BUILT AND PROVEN.** Real traffic egresses through a real proxy: VPN off -> ISP IP; VPN on -> the hop's IP. |
| ⚠️ **But multi-hop does not work** | **Webshare blocks proxy-to-proxy CONNECT (403).** The engine is correct; the inventory cannot chain. See **P25**. |
| **Repo** | `D:\project\Proxy`, branch `main`, **3 commits**, working tree clean |
| **Remote** | `git@github.com:SaidurRahman22/proxy_crack.git` — ⚠️ **not pushed yet** (SSH key not registered on GitHub) |
| **Backend tests** | **792 passing** |

---

## Component status

| Component | State | Detail |
|---|---|---|
| **Backend API** (`backend/`) | ✅ working | Django 5.2 + DRF, 14 models, 39 migrations, 792 tests |
| **Admin console** (`admin/`) | ✅ working | React + TS + Vite, runs against mock *or* live API |
| **Mobile app** (`app/`) | ✅ wired to live API | Flutter 3.47, real auth + real data, 46 tests. Engine still mocked (Phase 3) |
| **VPN engine** | ✅ working (1 hop) | Xray-core via libXray. TUN, consent, foreground service, DNS through tunnel, UDP black-hole, fail-closed. **Multi-hop blocked by the provider, not the code (P25).** |
| **Desktop clients** | ❌ not started | Windows / Linux / macOS planned (D8), Android first |
| **iOS** | ❌ blocked | Needs a Mac. Not buildable on this machine |

---

## 🔑 Credentials

**See [`ACCESS.md`](../ACCESS.md)** at the repo root — gitignored, never pushed.
It has the sign-in details for the app, the admin console and Django admin, plus how to grant a
plan and which env vars are required.

Short version: the mobile app needs the dev account listed in `ACCESS.md`, and that
account **must have a paid plan** — a free-tier account sees **zero** proxies, because the only
real proxy is `premium`. That is entitlement working, not a bug.

## Run it right now

```bash
# Backend + admin console
cd D:\project\Proxy\backend
.\.venv\Scripts\python.exe manage.py runserver 8000
#   Django admin (fallback):  http://127.0.0.1:8000/admin/
#   credentials: see ACCESS.md (gitignored)

# Admin SPA (mock data by default)
cd D:\project\Proxy\admin
npm run dev            # http://localhost:5173
#   admin@proxycrack.io / proxycrack   (mock credentials, shown on the login screen)
#   to point at live Django:  echo VITE_USE_MOCK_API=false > .env.development.local

# Mobile app
cd D:\project\Proxy\app
flutter run            # needs the emulator: desktop shortcut "Android Emulator"
```

### Operational commands

| Command | What it does |
|---|---|
| `probe_proxies` | Health-check proxies, write probe rows, rescore |
| `prune_probes` | Trim probe history (30-day default) |
| `prune_audit_log` | Trim audit history (365-day default, 90-day floor, dry-run by default) |
| `rotate_proxy_credentials` | Re-encrypt credentials under a new key |
| `refresh_ip_intel` | Refresh cached ASN / operator / country data |
| `seed_plans` | Create the free + pro plans |

---

## What has actually been verified (not assumed)

| | Evidence |
|---|---|
| Backend | 628 tests pass; `check` clean; no migration drift |
| Mobile build chain | Debug APK built, installed via adb, **rendered on emulator** |
| Admin SPA | `tsc` clean, production build, **screenshotted running against live Django** |
| Credentials encrypted at rest | Raw SQL shows Fernet tokens; no plaintext in the columns |
| Audit log is append-only | **5 attacks refused**, including raw SQL `UPDATE`/`DELETE` |
| Refresh-token rotation | Old token → 401 blacklisted; chain survives repeated rotation |
| Entitlement enforced server-side | Free user sees 2 of 4 proxies; `?tier=premium` returns 0 |
| Health scoring / flapping | 1 failure survives, 3 kill, 1 success revives — score stays depressed |

---

## Open items needing **your** decision

| # | Item | Why it matters |
|---|---|---|
| **P2** | Play Store **display name** | "Proxy Crack" reads as piracy tooling and risks rejection. `applicationId` is permanent; the display name is not. |
| **P4** | Which paid proxy provider, and their ToS | Many providers **forbid** redistribution via an app. This could reshape the product. |
| **P5** | **Wintun redistribution consent** | Needed for Windows TUN. Requires a lawyer. **Long lead time — start early.** |
| **P6** | Desktop payment rail | Play Billing is Android-only; one subscription must unlock four clients. |
| **P7** | UDP strategy | An HTTP hop cannot carry UDP. Any chain with one is TCP-only — no QUIC, VoIP, games. |
| **D18** | Credential round trips | `/resolve/` returns identities + a `credentials_url` per hop, so a 3-hop connect costs 3 extra calls. Collapsing it weakens the credential boundary. |

---

## Known gaps (tracked, not forgotten)

| # | Gap |
|---|---|
| **P8** | Credential keys live in the process environment — move to KMS before production |
| **P10** | No MFA on the admin console; 30-day refresh token in `localStorage` |
| **P11** | CSV import runs synchronously in a request worker |
| **P12** | No CSV export sanitisation rule yet (write it **before** someone adds the button) |
| — | **PostgreSQL audit trigger reviewed but never executed** — dev is SQLite. Validate before the Postgres move. |
| — | **No published test vector exists for the Shadowsocks composition.** Tested against a second implementation written from the same spec — weaker evidence than a published vector, and the test docstring says so. |
| — | **SS2022 (`2022-blake3-*`) not supported** — reported as `unsupported_cipher`, never as a dead proxy |

---

## Next steps, in order

1. ~~Push to GitHub~~ ✅ done — `origin/main` tracking, working tree clean
2. ~~R2 — diversity scoring~~ ✅ **DONE.** Detects when "three vendors" are one operator.
   Headline: `POST /api/admin/intel/candidates/` analyses hosts **not in inventory** — vet
   candidate proxies *before* buying. ⚠️ Needs `IP_INTEL_DNS_RESOLVERS` set to do anything.
3. **Wire the Flutter app to the real API.** ⬅️ agreed next, after R2.
   The mobile app currently has **no HTTP client at all** — it is a UI shell over
   `lib/data/mock/` with a `MockProxyEngine`. That was deliberate (built while the engine
   licence question was open) but it means the app cannot see real data. Needs:
   - typed API client in Dart against `/api/`
   - auth: login, JWT in memory, refresh-on-401, secure refresh-token storage
   - a real repository replacing `catalog_repository`'s mock, **behind the same interface**
     so no screen changes
   - loading / empty / error states wired to real failures
   Sequenced after R2 deliberately, because R2 adds a chain-diversity endpoint the chain
   builder should consume — wiring first would mean doing it twice.
4. **Phase 3 — the VPN engine.** The real risk. Everything before it is conventional work.
   Resolve **P5 (Wintun)** in parallel since it has an external lead time.

### Proxy inventory reality check
The dev DB holds **one** real proxy (Webshare, HTTP, premium), added by hand.

⚠️ **The Webshare free plan provides 10 IPs — only 1 is loaded**, and **nothing syncs
automatically**. When Webshare rotates or replaces an IP, this database does not notice. There
is no Webshare integration at all. See **R9** below.

Also:
- **one proxy is not a chain** — chaining needs ≥2 hops, and the product premise needs 3+
- **it is HTTP, so any chain containing it is TCP-only** — no QUIC, VoIP or games (P7)
- all hops from one vendor means **one operator sees both ends** — exactly what R2 detects

---

## The one thing to keep in mind

Everything built so far is the **supporting cast**: accounts, entitlement, proxy inventory,
health, an admin console. All of it is necessary and none of it routes a single packet.

The product is the engine, and the engine has not been started. The plan for it is in
[engine-decision.md](engine-decision.md), the licence question is settled (Xray-core, MPL), and
the hardest part — a userspace TCP/IP stack — is solved by not writing it ourselves.
