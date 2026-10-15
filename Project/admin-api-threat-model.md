# Threat Model — Admin API (`/api/admin/`)

**Date:** 2026-10-15 · **Version:** 1.0 · **Author:** Security Architect
**Target:** `Project/admin-api-contract.md` (the spec), `backend/adminapi/` (the implementation, in flux at time of writing)
**Grounded in:** Phase 1 code under `backend/` — `api/`, `billing/entitlements.py`, `proxies/`, `core/crypto.py`, `core/fields.py`, `core/settings.py`
**Related:** `Project/memory.md` D11 (credential encryption), D12 (SPA admin), P8 (key custody)

---

## 0. Why this surface is different

The mobile API at `/api/` is **scoped by construction**. `ProxyViewSet.get_queryset()` applies
the entitlement predicate before any client filter, credentials never appear in a list
serializer, and the one endpoint that decrypts them is throttled at 60/hour and audit-logged.
A permission failure there leaks one user's slice.

`/api/admin/` inverts every one of those properties on purpose: unscoped queryset, full record
access, credentials in the detail serializer, CRUD on the entitlement model itself. **There is
no second line behind the permission class.** The contract says this in Ground Rule 2; this
document takes it literally and asks what else must be true for that single control to be
enough — and what happens when it isn't.

One framing that shapes the ranking below: the attacker who matters most here is **not
anonymous**. Anonymous is well handled. The attacker who matters is a *valid staff credential
in the wrong hands* — phished, reused, or a departing contractor. Against that attacker, a
permission class is worth nothing, and every control that does work is a limit, a log, or a
privilege boundary *between* staff.

---

## 1. Assets and trust boundaries

### 1.1 Assets, ranked by what losing them costs

| # | Asset | Where it lives | Loss means |
|---|---|---|---|
| **A1** | **Decrypted proxy credentials** (`Proxy.username`, `.password`, `.ss_method`) | Fernet ciphertext in DB; plaintext in process memory and in every `/api/admin/proxies/{id}/` response | **The product.** The operator pays for these per-seat. A full dump is resellable, and the provider's ToS (P4) likely terminates the account. This is the only asset whose theft ends the business. |
| **A2** | **The entitlement model** (`Plan` rows) | `billing.Plan`, full CRUD on `/api/admin/plans/` | A single `PATCH` to the free plan's `max_proxy_tier` gives every free account the premium inventory through the *mobile* API. Revenue goes to zero without one credential leaving the admin surface. |
| **A3** | **Superuser account control** | `core.User.is_superuser`, `/api/admin/users/{id}/set_password/` | Full Django Admin at `/admin/`, shell-adjacent access, ability to mint staff accounts, ability to turn off every control in this document. |
| **A4** | **`PROXY_CRED_KEY` / `JWT_SIGNING_KEY` / `DJANGO_SECRET_KEY`** | Process environment (P8 — not yet in a KMS) | A1 at rest (key), or the ability to forge any identity (JWT key). `JWT_SIGNING_KEY` defaults to `SECRET_KEY`. |
| **A5** | **User PII + behavioural data** | `User.email`, `UsageLog`, `ProxyChain`/`ChainHop` | For a VPN product this is the data users assume does not exist. `/api/admin/chains/` and `/api/admin/usage-logs/` make a per-user "which exit nodes, when, how much" dataset bulk-readable. A breach here is a privacy incident with regulatory weight, not just a support problem. |
| **A6** | **Subscription / billing state** | `billing.Subscription`, full CRUD | Free premium (fraud), or mass revocation (denial of service against paying users). |
| **A7** | **Operator content** | `core.Content` | Low value alone. Material as an XSS vector into a *higher-privileged* staff browser — see T11. |
| **A8** | **Availability of the mobile API** | shared Django process / DB | Admin CSV import and stats aggregation run synchronously in the same worker pool that serves paying users. |

### 1.2 Actors

| Actor | Has | Should reach | Realistic threat |
|---|---|---|---|
| **Anonymous** | nothing | `/api/admin/auth/login/` only | Credential stuffing; account-existence probing; unauthenticated endpoint discovery |
| **End user (valid mobile JWT)** | a token signed by the same key, for the same user model | `/api/` scoped only | **The headline risk.** Their token is cryptographically indistinguishable from a staff token. Only `is_staff` on the row stands between them and A1. |
| **Staff (`is_staff=True`)** | full admin API | everything except privilege changes and superuser impersonation | The contract grants a flat `is_staff` role with *no internal boundary*. Today staff = superuser in practice (see T2). |
| **Superuser** | staff + `/admin/` + Django's own permission system | everything | Accepted. Controls here are detective (audit), not preventive. |
| **Compromised staff account** | everything staff has, with no account recovery signal | — | The actor the design must survive. Phished password, reused password, stolen `localStorage` refresh token, stolen laptop. |
| **Malicious insider (staff)** | same, plus knowledge of what's valuable and when nobody is looking | — | Differs from the above only in that they will prefer the *quiet* paths: entitlement flip over bulk download, export over detail iteration. |
| **Compromised SPA origin** | whatever XSS yields: in-memory access token, 30-day refresh token from `localStorage` | — | Turns any stored-XSS sink into persistent admin access. |
| **Proxy provider / upstream** | sees the operator's usage of their credentials | — | Out of scope, but ToS termination (P4) is a real consequence of A1 leaking. |

### 1.3 Trust boundaries

```
                    ┌──────────────────────────────────────────────┐
  Internet          │                                              │
  ───────────────── │ ── B1 ─────────────────────────────────────  │
                    │                                              │
   ┌────────────┐   │   ┌──────────────┐      ┌─────────────────┐  │
   │ Mobile app │───┼──▶│  /api/       │      │  /admin/        │  │
   │ (end user) │   │   │  entitlement │      │  Django Admin   │  │
   └────────────┘   │   │  SCOPED      │      │  session cookie │  │
                    │   └──────┬───────┘      └────────┬────────┘  │
   ┌────────────┐   │          │                       │           │
   │ Admin SPA  │───┼── B2 ──▶ │   ┌───────────────┐   │           │
   │ (browser)  │   │          │   │ /api/admin/   │   │           │
   └────────────┘   │          │   │  UNSCOPED     │   │           │
                    │          │   │  IsStaffUser  │   │           │
                    │          │   └───────┬───────┘   │           │
                    │          │           │           │           │
                    │  ── B3 ──┴───────────┴───────────┴────────   │
                    │                      │                       │
                    │            ┌─────────▼──────────┐            │
                    │            │  Django ORM        │            │
                    │  ── B4 ────┤  EncryptedCharField│            │
                    │            │  (Fernet, MultiF.) │            │
                    │            └─────────┬──────────┘            │
                    │                      │                       │
                    │            ┌─────────▼──────────┐            │
                    │            │  Database          │            │
                    │            │  ciphertext at rest│            │
                    │            └────────────────────┘            │
                    │  PROXY_CRED_KEY ── env (P8) ──── B5          │
                    └──────────────────────────────────────────────┘
```

| Boundary | From → To | Control today | Gap |
|---|---|---|---|
| **B1** Internet → app | anyone → any endpoint | TLS (`SECURE_SSL_REDIRECT`/HSTS when `DEBUG=False`), `ALLOWED_HOSTS`, DRF anon throttle | `DEBUG` **defaults to `True`** when the env var is absent — see T6 |
| **B2** Browser origin → API | SPA → `/api/admin/` | **Nothing specified.** No CORS config, `django-cors-headers` is not in `requirements.txt` | T12 |
| **B3** Authenticated → authorised | any valid JWT → admin data | `adminapi.permissions.IsStaffUser` (checks `is_authenticated`, `is_active`, `is_staff`) — **the only control**, and it is one class | No token-level separation between mobile and admin tokens; same signing key, no audience claim. T5 |
| **B3′** Staff → superuser | staff → privilege fields | **Nothing in the contract.** `is_staff`/`is_superuser` are listed as plain CRUD fields | T2 — the single worst contract defect |
| **B3″** Admin API → entitlement model | staff → `Plan` | **Nothing.** Plan is ordinary CRUD | T3 |
| **B4** ORM → DB | plaintext → ciphertext | `core/crypto.py` + `core/fields.py`, fail-loud via `core/checks.py`. **Carries over unchanged** — the admin API writes through the same fields | Decrypt happens in-process, so B4 protects dumps/replicas only (D11's stated scope, P8) |
| **B5** Process → key custody | app → `PROXY_CRED_KEY` | env var, `MultiFernet` rotation supported, `rotate_proxy_credentials` command exists | P8 open: code execution on the host yields the key |

---

## 2. STRIDE — concrete to these endpoints

### Spoofing

| # | Where | Scenario | Existing control | Verdict |
|---|---|---|---|---|
| S1 | `POST /api/admin/auth/login/` | Credential stuffing against staff emails harvested from the public site / GitHub commits | Contract requires a uniform 403 (`AdminLoginFailed` already implements this). `THROTTLE_LOGIN=20/hour` exists but is the *mobile* scope | **Gap:** no admin-specific login rate, no lockout, **no MFA**. 20/hour per IP across a botnet is ample. |
| S2 | login | Account-existence oracle via response **timing**, not status code | `AdminLoginFailed` equalises the *message*. Django's `ModelBackend.authenticate()` equalises timing by hashing a dummy password on miss — **only if `authenticate()` is used** | Verify the view does not short-circuit on `User.objects.get()` or on `is_staff` before password verification |
| S3 | any admin endpoint | End-user JWT presented as a staff token | `IsStaffUser` | Works, but it is the *only* thing working. The token itself is valid. See T5. |
| S4 | any admin endpoint | Forged JWT signed with a guessed key | `JWT_SIGNING_KEY` → `SECRET_KEY` → **`"dev-insecure-change-me"` if env is absent** | **Gap.** See T6. |
| S5 | `POST /api/admin/users/{id}/set_password/` | Staff sets the superuser's password and logs in as them | None in the contract | See T2. |

### Tampering

| # | Where | Scenario | Verdict |
|---|---|---|---|
| T-a | `PATCH /api/admin/users/{id}/` | `{"is_superuser": true}` on self or on an attacker-created account | **Live in the current draft** — `is_staff`/`is_superuser` are in `fields` and not in `read_only_fields`. T2. |
| T-b | `PATCH /api/admin/plans/{free}/` | `{"max_proxy_tier": "premium", "max_chain_length": 10}` | Instantly re-entitles the entire free user base. No guard, no confirmation, no audit requirement. T3. |
| T-c | `POST /api/admin/proxies/upload_csv/` | CSV with only `protocol,host,port` columns | `importers._clean_row` always writes `username`/`password`/`ss_method` into `defaults`, so `update_or_create` **blanks the credentials of every matching existing row**. Silent destruction of A1. T8. |
| T-d | `POST /api/admin/proxies/bulk_delete/` | Unbounded `ids[]` | Contract states no cap. `ChainHop.proxy` is `PROTECT`, so one referenced id raises `ProtectedError` and the whole batch 500s — the contract's `{deleted: n}` has no partial-failure shape. T13. |
| T-e | `PATCH /api/admin/subscriptions/{id}/` | Grant self (or a friend) a premium subscription with `expires_at` far in the future | Authorised-by-design for staff. Only detectable, and only if audited. |
| T-f | `PUT` with mass assignment | A `ModelSerializer` with `fields = "__all__"` on `User` would expose `password` (hash), `groups`, `user_permissions` — `groups` is a second, quieter escalation path than `is_superuser` | DRF docs explicitly warn against `__all__` for exactly this. Checklist C-12. |

### Repudiation

| # | Scenario | Verdict |
|---|---|---|
| R-a | A staff member denies exfiltrating credentials | Phase 1 has **one** audit line — `logger.info("credential_access ...")` in `ProxyViewSet.retrieve` — to a **console handler only**. No file, no syslog, no off-host shipping, no retention. Unattributable after the container restarts. |
| R-b | A staff member denies changing a plan, deleting proxies, or resetting a password | **No audit trail at all is specified.** And D12 *removes* one that exists: replacing Django Admin with the SPA abandons `django.contrib.admin.models.LogEntry`, which today records every create/change/delete made at `/admin/` with actor, object and a change message. **Nobody wrote this consequence down.** T10. |
| R-c | Which staff account was used, from where | No IP, user-agent or request-id in any log line today |

### Information disclosure

| # | Where | Scenario | Verdict |
|---|---|---|---|
| I-a | `GET /api/admin/proxies/{id}/` iterated over `id` | **Mass credential exfiltration.** The named #1 concern, and correctly named | T4 — and the throttle meant to stop it does not work as configured (T1) |
| I-b | any list endpoint | A future `?expand=credentials`, an "export CSV" button, or `ProxyDetailSerializer` reused for `list` | One line of code turns a 7-hour scrape into one request. Needs a structural guard, not a code-review habit. C-07. |
| I-c | `?ordering=` | `ordering_fields = "__all__"` lets DRF order by related fields — `?ordering=user__password` is a slow but real oracle over password hashes | Enumerate `ordering_fields` explicitly. C-14. |
| I-d | `GET /api/admin/upload-batches/` | `UploadBatch.errors` embeds `f"line {n}: {exc.__class__.__name__}: {exc}"` from the DB-rejection branch. An unexpected exception can carry row content — including a `password` column value — into a field the SPA renders and an operator may export | Low likelihood, non-zero. Sanitise the DB-exception branch. C-25. |
| I-e | error responses | `DEBUG=True` renders Django's stack-trace page with settings, env and SQL | Defaults to `True`. T6. |
| I-f | `GET /api/admin/chains/`, `/usage-logs/` | Bulk per-user behavioural data, `?ids=` up to 1000 per request, no throttle, no masking, no retention policy | A5. Privacy exposure on a product whose value proposition is privacy. T15. |

### Denial of service

| # | Where | Scenario | Verdict |
|---|---|---|---|
| D-a | `POST /api/admin/proxies/upload_csv/` | `importers.import_proxies_from_csv` does `raw = file_obj.read()` then `io.StringIO(text)` — **the whole file in memory twice**. The 5 MB limit lives in `proxies/forms.ProxyCSVUploadForm`, a *Django admin form*, which the DRF endpoint does not use. `DATA_UPLOAD_MAX_MEMORY_SIZE` does **not** apply to uploaded files | **Unbounded memory on the new path unless re-enforced.** T7. |
| D-b | upload | 50 000 rows × one savepoint + SELECT + INSERT each, synchronous, in a request worker | Minutes of a blocked worker. Shared pool with the paying mobile API. |
| D-c | `GET /api/admin/stats/` | Unbounded aggregates over `UsageLog`, `Proxy`, `Subscription` on every dashboard load/poll | Low; bound it with a short cache. |
| D-d | login | `UPDATE_LAST_LOGIN: True` is set. SimpleJWT's own docs warn this "will dramatically increase the number of database transactions" and that abuse "could slow the server and this could be a security vulnerability" | Mitigated by a login throttle — which the admin login currently lacks. |
| D-e | throttles generally | Counters live in Django's default cache. **`CACHES` is not configured**, so that is per-process `LocMemCache` | Every throttle in the product is silently multiplied by worker count and resets on deploy. T1. |

### Elevation of privilege

| # | Scenario | Verdict |
|---|---|---|
| E-a | staff → superuser via `is_superuser` write | T2. Live in the draft. |
| E-b | staff → superuser via `set_password` on the superuser | T2. |
| E-c | staff → superuser via `groups` / `user_permissions` mass assignment | T2 variant; only reachable with `fields = "__all__"`. |
| E-d | end user → staff | Requires a missing permission class. The `adminapi.checks` startup check (referenced by `apps.py`, **file does not exist yet**) is the right structural answer. C-01. |
| E-e | XSS in the SPA → superuser's in-memory access token → `PATCH is_superuser` on the attacker's own account | T11 — chains A7, B2 and B3′ into a complete escalation. |
| E-f | staff → host | `set_password` on the superuser → log in at `/admin/` → Django Admin is still mounted (contract item 4) → it is a much broader write surface than the admin API, including `groups`/`permissions`. Keeping `/admin/` is correct as a fallback, but it means **the admin API's privilege boundary is only as good as Django Admin's**. |

---

## 3. Ranked threats

Likelihood is "within the first year of production, given this design". Impact is to the business, not to a CVSS vector. Items marked *theoretical* are ranked low on purpose and are not padding — they are listed so a reviewer does not spend time on them.

| # | Threat | Likelihood | Impact | Risk | Specific to this design? |
|---|---|---|---|---|---|
| **T1** | **Throttle counters are per-process because `CACHES` is unset** — the 60/hour credential limit, the login limit and every admin limit are multiplied by worker count and reset on every deploy | **Certain** (true in the repo now) | High — it is the primary anti-scrape control for A1 on *both* APIs | **Critical** | Yes |
| **T2** | **Staff self-escalation to superuser** via `PATCH /api/admin/users/{id}/ {"is_superuser": true}`, or by `set_password` on the superuser | High (one compromised or disgruntled staff account) | Critical — A3, then everything | **Critical** | Yes — the contract lists the flags as plain CRUD fields |
| **T3** | **Entitlement flip:** `PATCH` the free plan's `max_proxy_tier` to `premium`, then scrape A1 through the **mobile** API with throwaway free accounts | Medium | Critical — total credential loss **with no admin audit line and no admin throttle touched** | **Critical** | Yes — unique to having one DB serve a scoped and an unscoped API |
| **T4** | **Mass credential exfiltration** by iterating `/api/admin/proxies/{id}/` | Medium–High | Critical — A1 | **Critical** | Yes (named in the brief; correctly) |
| **T5** | **Single-control dependency:** an end-user JWT is cryptographically valid for the admin API; only `IsStaffUser` distinguishes it. Same signing key, no `aud`, no admin-scope claim | Low per-endpoint, but the blast radius of one omission is total | Critical | **High** | Yes |
| **T6** | **Fail-open deployment defaults:** `DEBUG` defaults to `True` and `SECRET_KEY` to `"dev-insecure-change-me"` when `.env` is missing; `JWT_SIGNING_KEY` defaults to `SECRET_KEY`. A JWT signed with the published default authenticates as `user_id=1` | Low–Medium (one bad deploy) | Critical — forged superuser identity + stack traces | **High** | Partly — but note `PROXY_CRED_KEY` *does* fail loud via `core/checks.py`, so the pattern exists and was simply not applied to these |
| **T7** | **CSV memory exhaustion:** the 5 MB cap lives in a Django admin form the DRF endpoint will not use; `read()` loads the whole file twice | Medium | Medium–High — OOM kills the worker serving paying users | **High** | Yes |
| **T8** | **Credential blanking by partial-column CSV:** `update_or_create` writes empty `username`/`password` over existing rows when those columns are absent | Medium (an honest mistake, not an attack) | High — silent destruction of A1, no backup of plaintext exists by design | **High** | Yes — a Phase 1 bug the SPA makes far easier to hit |
| **T9** | **No MFA on a console that holds the company's entire sellable asset**, plus a 30-day refresh token in `localStorage` | Medium | Critical | **High** | No (generic), but the asset makes it acute |
| **T10** | **Audit regression:** D12 abandons `django.contrib.admin.LogEntry` and replaces it with nothing; Phase 1 logging is one `logger.info` to stdout | Certain | High — no attribution, no detection, no incident reconstruction | **High** | Yes |
| **T11** | **Stored XSS → privilege escalation:** staff writes a payload into `Content.body`; a superuser previews it in the SPA; the payload uses the in-memory access token to `PATCH is_superuser` | Low–Medium (needs `dangerouslySetInnerHTML` or a markdown renderer) | Critical | **Medium–High** | Yes — chains three otherwise-minor items |
| **T12** | **CORS misconfiguration:** nothing specified, package not installed. The likely first attempt is `CORS_ALLOW_ALL_ORIGINS = True` (+ `CORS_ALLOW_CREDENTIALS = True`), or an unanchored regex | Medium | Medium — with `CORS_ALLOW_CREDENTIALS=False` and token-only auth the damage is bounded; it becomes High if `SessionAuthentication` is ever added, since `/admin/` session cookies are still live | **Medium** | No, but the still-mounted `/admin/` raises it |
| **T13** | **Unbounded `bulk_delete` / `bulk_enable`**: no cap on `ids[]`, no partial-failure shape, `PROTECT` FKs turn one bad id into a 500 | Medium | Medium — destructive and confusing; reversible only from backups | **Medium** | Yes |
| **T14** | **Access tokens survive logout and password reset** for up to 30 minutes. SimpleJWT's blacklist covers *refresh* tokens only | Medium | Medium — `IsStaffUser`'s explicit `is_active` check already closes the *deactivation* half; the stolen-token half stays open | **Medium** | No — but the existing `is_active` check is a good, deliberate partial mitigation and should be called out as such |
| **T15** | **Privacy exposure of A5**: bulk, unthrottled, unmasked access to per-user chain and session data on a VPN product, with no retention policy | Medium | Medium–High (regulatory / reputational) | **Medium** | Yes |
| **T16** | **CSV formula injection in a future export**: `=`,`+`,`-`,`@`,TAB,CR in `host`/`city`/`errors` flow from an uploaded CSV into an exported one | Low today (**no export endpoint exists in the contract**) | Medium — RCE on an operator's workstation | **Low–Medium** | Pre-emptive; the gap is that the contract never says exports must sanitise |
| **T17** | **`?ordering=` oracle** over related fields including password hashes, if `ordering_fields = "__all__"` | Low | Low–Medium | **Low** | No |
| **T18** | **JWT algorithm confusion / `alg:none`** | *Theoretical.* SimpleJWT 5.5.1 passes an explicit single-algorithm list to PyJWT 2.15.1; `none` and RS256→HS256 substitution are rejected by the library. Worth one regression test, not worth design effort | Critical if true | **Low** | No |
| **T19** | **Path traversal via upload filename** | *Theoretical.* `UploadedFile` basenames the name, and `filename` is only stored as text — nothing is written to disk. Becomes real the moment anyone adds "keep the original file" or a download endpoint | Medium | **Low** (regression guard only) | No |
| **T20** | **Zip bomb** | *Not applicable.* No archive handling; the importer takes raw CSV only. The analogous risk is the plain oversized CSV (T7). Listed only to close it out | — | **None** | — |
| **T21** | **Classic IDOR** (read another user's object by changing an id) | *By design, for staff.* The admin API is unscoped; "IDOR" here is only meaningful as (a) `set_password` across a privilege boundary (T2) and (b) credential enumeration (T4). There is no horizontal boundary left to violate | — | **N/A as stated** | — |

---

## 4. Attack scenarios worth writing tests for

These are written as test intentions, not code. Each should be a failing test first.

### 4.1 End-user JWT against admin endpoints

- **A-01** Parametrise over **every route the admin router produces** — including `@action` routes (`set_password`, `cancel`, `bulk_enable`, `bulk_delete`, `upload_csv`) and every HTTP method — and assert an end-user JWT gets **403**, never 200/404/405. Build the list from `router.urls` + the extra `path()`s, not from a hand-written list, so a new endpoint fails the test by existing.
- **A-02** Same sweep with **no** credentials → 401.
- **A-03** Same sweep with a staff JWT whose user was since set `is_active=False` → 403. (Confirms `IsStaffUser`'s explicit `is_active` check; this one should pass today.)
- **A-04** Same sweep with a staff JWT whose user was since set `is_staff=False` → 403, without waiting for token expiry.
- **A-05** A **404 must not be reachable before a 403**: request `/api/admin/users/999999/` as an end user and assert 403, not 404 (a 404 confirms the route exists and the permission ran after object lookup).
- **A-06** Assert the startup check actually fires: construct a view under `/api/admin/` without `IsStaffUser` in a test app and assert `manage.py check` errors.

### 4.2 Privilege escalation via user CRUD

- **B-01** Staff `PATCH`es **their own** user with `{"is_superuser": true}` → field ignored or 403; re-read confirms `is_superuser` is still `False`.
- **B-02** Staff `PATCH`es **another** user with `{"is_superuser": true}` → same.
- **B-03** Staff `POST`s a **new** user with `{"is_staff": true, "is_superuser": true}` → flags not honoured.
- **B-04** Staff `PATCH`es `{"is_staff": true}` on an ordinary end user → rejected (minting staff is a superuser action).
- **B-05** Superuser performs B-01..B-04 → succeeds. (Confirms the restriction is conditional, not blanket.)
- **B-06** Staff calls `set_password` on a **superuser** → 403. Target's password unchanged.
- **B-07** Staff calls `set_password` on another **staff** user → 403 (lateral takeover is escalation too).
- **B-08** Staff calls `set_password` on an ordinary user → allowed, **and** the target's outstanding refresh tokens are blacklisted.
- **B-09** `set_password` rejects a password failing `AUTH_PASSWORD_VALIDATORS` (min length 10 etc.) — the mobile `RegisterSerializer` validates; the admin path must not be the weak one.
- **B-10** `GET`/`PATCH` on users never returns `password`, and `password` is not writable (a writable `password` on a `ModelSerializer` stores the raw string and silently bricks the account).
- **B-11** `groups` and `user_permissions` are absent from the serializer, or read-only to non-superusers.
- **B-12** A superuser cannot remove their **own** last-superuser status (lockout guard) — low severity, but it is the kind of thing only a test catches.

### 4.3 Mass credential exfiltration

- **C-01** Iterate `GET /api/admin/proxies/{id}/` as staff and assert the **N+1th request in the window is 429**, with N the documented admin credential budget. Run it with the cache backend the production config uses, not `LocMemCache`.
- **C-02** `GET /api/admin/proxies/` (list) response contains **no** `username`, `password` or `ss_method` key — assert on key absence, for every row, for `page_size=200`.
- **C-03** `?ids=` with 200 ids on the proxy list still returns no credentials.
- **C-04** No query parameter widens the list serializer: sweep `?expand=`, `?fields=`, `?format=`, `?include=` and assert credentials stay absent.
- **C-05** `GET /api/admin/proxies/?format=csv` (and any other registered renderer) → 404/406, not a credential dump.
- **C-06** `?page_size=100000` is clamped to 200.
- **C-07** Every credential disclosure emits exactly one audit record containing actor id, proxy id, and source IP — including on `create` and `update` responses, which echo the credentials back.
- **C-08** `/api/admin/upload-batches/` never returns a credential substring: upload a CSV whose password column triggers a DB-level error, then assert the stored `errors` text does not contain that password.

### 4.4 Entitlement flip (T3) — the cross-surface test

- **D-01** As staff, `PATCH` the free plan to `max_proxy_tier="premium"`. Then, as a **free end user on `/api/`**, assert the premium inventory is *not* reachable without a deliberate, separately-authorised change — or, if the product accepts that a plan edit does exactly this, assert the change emits a **high-severity audit event** naming the before and after values.
- **D-02** Assert `PATCH` on `Plan.max_proxy_tier`, `max_chain_length`, `is_free_tier` and `is_active` each produce an audit record with old and new values.
- **D-03** Assert the free-plan row cannot be made inactive while it is the only `is_free_tier=True, is_active=True` row (otherwise every free user silently falls to `ENTITLEMENT_FALLBACK`).

### 4.5 Plan-deletion guard

- **E-01** Delete a plan with an active subscription → **409**, body carries the integer count (not `"3"` — `adminapi.exceptions.Conflict` exists precisely to preserve this; test the type).
- **E-02** Delete a plan whose only subscriptions are `expired`/`cancelled` → `PlanInUse` 409, **not** a 500 from `ProtectedError` (`Subscription.plan` is `on_delete=PROTECT`).
- **E-03** **Guard bypass via a generic path:** attempt the delete through `?ids=` + any bulk route, and through a `PUT`/`PATCH` that sets `is_active=False` — assert the guard is in `perform_destroy`/model layer, not only in the detail `DELETE` handler.
- **E-04** **Guard bypass by clearing the obstacle:** staff cancels all subscriptions, then deletes the plan. This **succeeds** and that is correct. The test asserts the sequence is fully audited — the guard protects against accident, not against intent, and the control for intent is the log.
- **E-05** Race: concurrent `POST /subscriptions/` and `DELETE /plans/{id}/` → one of them fails cleanly with 409, neither 500s.

### 4.6 JWT handling

- **F-01** Token with `alg: none` → 401.
- **F-02** Token signed with RS256 using the HS256 secret as a public key (algorithm confusion) → 401.
- **F-03** Expired access token → 401.
- **F-04** Token with `exp` removed → 401.
- **F-05** Refresh rotation: use a refresh token, then **reuse the old one** → 401 (confirms `BLACKLIST_AFTER_ROTATION` + the `token_blacklist` migrations actually ran — blacklisting silently does nothing if the app is installed but unmigrated).
- **F-06** `logout` blacklists the refresh token; a subsequent refresh with it → 401.
- **F-07** **Document the known gap:** after `logout`, the *access* token still works until expiry. Assert the measured window equals `ACCESS_TOKEN_LIFETIME` so a future lifetime increase is a visible test change, and decide whether `CHECK_REVOKE_TOKEN` should close the password-change half (it ties the token to a hash of the current password).
- **F-08** A token minted for a non-staff user, then the user is promoted to staff — assert it works immediately (no stale-claim behaviour), which confirms `is_staff` is read from the DB per request and not from a claim.
- **F-09** If an admin-scope claim is adopted (recommended, C-04): a mobile-issued token without the claim → 401 at the authentication layer, **before** any permission class runs.

### 4.7 CSV upload abuse

- **G-01** Upload a file larger than `PROXY_CSV_MAX_BYTES` → 400 **before** `read()` is called. Assert on `uploaded.size` and on peak memory, not just the status code.
- **G-02** Upload a well-formed CSV with `PROXY_CSV_MAX_ROWS + 1` rows → import stops at the limit and reports it.
- **G-03** Upload non-UTF-8 bytes → `CSVImportError` 400, not a 500.
- **G-04** Upload a file named `../../../../etc/passwd.csv` → stored `filename` contains no path separators; assert nothing is written outside the upload temp dir. (Currently passes trivially — this is a **regression guard** for when someone adds file retention.)
- **G-05** Upload with content-type `image/png` and `.csv` extension, and vice versa → the extension/type check is enforced on the API path, not only in `ProxyCSVUploadForm`.
- **G-06** **T8:** given an existing proxy with credentials, upload a CSV containing only `protocol,host,port` for that endpoint → assert the existing `username`/`password` are **preserved**. This test should fail against today's `importers.py`.
- **G-07** Upload a row whose `tier` column is absent → row lands as `premium` (fail-safe default already in `importers.py`; lock it in).
- **G-08** **CSV injection, pre-emptive:** store a proxy with `city = '=cmd|\'/c calc\'!A1'`; if/when any export endpoint exists, assert the exported cell is prefixed (single quote) and that the check covers `=`, `+`, `-`, `@`, TAB (0x09), CR (0x0D) and LF (0x0A) — OWASP's current list — including after a leading separator or quote character.
- **G-09** A 5 MB single-line CSV (one row, millions of fields) completes or is rejected within a bounded time — not a hang.
- **G-10** The upload endpoint has its own throttle scope; the 11th upload in an hour is 429.

### 4.8 CORS

- **H-01** `OPTIONS` preflight from an unlisted origin → no `Access-Control-Allow-Origin` header in the response.
- **H-02** `Origin: null` (sandboxed iframe, `file://`) → not allowed.
- **H-03** If a regex is used, assert `https://admin.example.com.evil.com` and `https://evilexample.com` are **rejected** and the pattern is anchored with `^` and `$`.
- **H-04** `Access-Control-Allow-Credentials` is **absent or false** — the design puts the token in the `Authorization` header, so credentials must never be enabled; this is what keeps the still-live `/admin/` session cookie out of reach cross-origin.
- **H-05** `Vary: Origin` is present on every CORS response (cache-poisoning guard).
- **H-06** `SessionAuthentication` is **not** in the admin API's authentication classes. Assert a request carrying only a valid Django `sessionid` cookie from `/admin/` gets 401 at `/api/admin/users/`.

### 4.9 Bulk endpoints

- **I-01** `bulk_delete` with 10 001 ids → 400, not a 10 000-row delete.
- **I-02** `bulk_delete` where one id is referenced by a `ChainHop` → structured 409/partial result, not a 500 `ProtectedError`, and **nothing else in the batch is silently lost**.
- **I-03** `bulk_enable` with a non-integer id → 400.
- **I-04** Both bulk endpoints emit one audit record naming the actor and the full id list.

---

## 5. Controls checklist

Audit the implementation against this once the backend agent finishes. Each line is pass/fail as written. "Carried over" means the control exists in Phase 1 code and must keep working on the new surface.

### Authorisation

| # | Control | Pass criterion |
|---|---|---|
| C-01 | Every admin view inherits a shared base with `permission_classes = (IsAuthenticated, IsStaffUser)`; **no view sets its own** except the two `AllowAny` auth endpoints | Grep `adminapi/views.py` for `permission_classes` — exactly three occurrences (base, login, refresh) |
| C-02 | `adminapi/checks.py` exists, is imported by `apps.py` (**it is imported today but the file does not exist** — this is an `ImportError` at startup, fix before review), and walks the resolved URLconf asserting `IsStaffUser` on every `/api/admin/` route including `@action` routes | `manage.py check` fails when a route is missing it (test A-06) |
| C-03 | `IsStaffUser` checks `is_authenticated`, `is_active` **and** `is_staff` | Carried over — already correct in `adminapi/permissions.py`, including the `is_active` reasoning. Do not weaken it |
| C-04 | **Recommended addition:** admin tokens carry a distinguishing claim (e.g. `scope: "admin"`), minted only by `/api/admin/auth/login/`, and the admin API uses an authentication class that rejects tokens without it | A mobile-issued token is rejected at the *authentication* layer, before permissions. Converts "one missing permission class = total loss" into "two independent failures required". Closes T5 |
| C-05 | `is_superuser` is read-only unless `request.user.is_superuser` | Test B-01, B-02, B-05 |
| C-06 | `is_staff` is read-only unless `request.user.is_superuser` | Test B-04 |
| C-07 | `set_password` refuses any target where `target.is_superuser` or (`target.is_staff` and requester is not superuser), and refuses self unless the current password is supplied | Tests B-06..B-08 |
| C-08 | `set_password` runs `AUTH_PASSWORD_VALIDATORS` and blacklists the target's outstanding refresh tokens | Tests B-08, B-09 |
| C-09 | `groups` / `user_permissions` are absent from the user serializer, or superuser-only | Test B-11 |
| C-10 | `password` is never readable and never writable on the user serializer | Test B-10. Contract already says "never return password hashes" — this extends it to writes |

### Serialisation and data exposure

| # | Control | Pass criterion |
|---|---|---|
| C-11 | No serializer uses `fields = "__all__"` or `exclude` | Grep. DRF's own docs recommend against it for exactly this reason |
| C-12 | Privilege fields are restricted via explicit `serializers.BooleanField(read_only=...)` logic or `get_fields()`, **not** `Meta.read_only_fields` if the field is also declared on the class — DRF documents that `read_only_fields` is ignored for explicitly declared fields | Read the serializer; confirm the mechanism, then confirm with tests B-01..B-05 |
| C-13 | The proxy **list** serializer does not inherit from, and is not swappable with, the detail serializer. Prefer the inverse of Phase 1's inheritance: make the credential fields an explicit opt-in on one serializer rather than `ProxyDetailSerializer(ProxyListSerializer)` + extra fields, so a `get_serializer_class` mistake fails closed | Tests C-02..C-06 |
| C-14 | `ordering_fields` and `search_fields` are explicit tuples, never `"__all__"`, and contain no related-model traversal into `User` | Grep; test I-c / C-14 |
| C-15 | `search_fields` contain no `EncryptedCharField` | Carried over — `EncryptedCharField.get_lookup` raises `FieldError` on anything but `isnull`, and `adminapi/filters.py` already documents this. It fails loudly (500), not silently (empty result) — acceptable, but keep it out of the field lists |
| C-16 | `?ids=` is capped | Carried over — `IdsFilterBackend.MAX_IDS = 1000` in `adminapi/filters.py`. Confirm the same cap applies to `bulk_delete`/`bulk_enable` **request bodies**, which do not go through the filter backend |
| C-17 | Pagination `max_page_size = 200` enforced on every list | Carried over — `AdminPagination`. Test C-06 |
| C-18 | No renderer other than JSON (and the browsable API in `DEBUG` only) is registered | Test C-05 |

### Authentication and tokens

| # | Control | Pass criterion |
|---|---|---|
| C-19 | `/api/admin/auth/login/` returns an identical 403 for unknown email, wrong password, and correct-password-non-staff | Carried over by design in `adminapi/exceptions.AdminLoginFailed`. Verify the **view** does not short-circuit before password verification (test S2) |
| C-20 | The login view uses `django.contrib.auth.authenticate()` so the dummy-hash timing equalisation applies | Read the view |
| C-21 | `JWT_SIGNING_KEY` is set **independently** of `DJANGO_SECRET_KEY` in production. SimpleJWT's docs explicitly recommend this | `.env` review + a deploy check |
| C-22 | Admin refresh lifetime is materially shorter than the mobile 30 days (recommend ≤ 24h for staff) | Requires a separate token class for admin, since `SIMPLE_JWT` is global |
| C-23 | `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION` on, `token_blacklist` installed **and migrated** | Carried over in `core/settings.py`. Test F-05 — blacklisting silently no-ops if the app is installed but unmigrated |
| C-24 | `flushexpiredtokens` is scheduled | `token_blacklist_outstandingtoken` grows unbounded otherwise |
| C-25 | Consider `CHECK_REVOKE_TOKEN = True` so a password change invalidates outstanding access tokens | Decision recorded either way; test F-07 documents the window |
| C-26 | MFA on staff accounts (TOTP at minimum) | **Currently absent and not in the contract.** If deferred, record it as a dated, accepted risk — not an oversight |

### Rate limiting (see §6 for numbers)

| # | Control | Pass criterion |
|---|---|---|
| C-27 | **`CACHES` is configured to a shared backend** (Redis/Memcached) in any multi-process deployment | **Currently unset in `core/settings.py` → `LocMemCache` → every throttle in the product is per-process.** This is the single highest-value one-line fix in this document |
| C-28 | A dedicated `admin_proxy_credentials` throttle scope exists with a deliberately chosen number — **not** a reuse of the mobile `proxy_credentials` 60/hour, which would make the SPA unusable, and not the default `user` 120/min, which allows 7 200 credential reads an hour | Test C-01 |
| C-29 | `admin_login` scope exists, keyed on IP **and** on the submitted email | Prevents both distributed stuffing and one attacker locking out all admins |
| C-30 | `admin_csv_upload` scope exists | Test G-10 |
| C-31 | `/api/admin/auth/refresh/` is throttled | |

### CSV upload

| # | Control | Pass criterion |
|---|---|---|
| C-32 | File size is checked **before** `file_obj.read()`, on the DRF path, against `PROXY_CSV_MAX_BYTES` | Test G-01. Note `DATA_UPLOAD_MAX_MEMORY_SIZE` does **not** cover uploaded files |
| C-33 | `PROXY_CSV_MAX_ROWS` enforced | Carried over inside `import_proxies_from_csv` |
| C-34 | Extension/content-type validated on the DRF path (the existing check lives in `proxies/forms.py`, which the API will not use) | Test G-05 |
| C-35 | `importers._clean_row` does not write empty credential values over existing rows when the column is absent | **Fails today.** Test G-06 — T8 |
| C-36 | Tier defaults to `premium` when absent | Carried over — correct fail-safe, keep it |
| C-37 | The DB-exception branch in `importers.py` does not echo row values into `UploadBatch.errors` | Test C-08 |
| C-38 | Import either runs out-of-band (queue/management command) or has a hard wall-clock budget | T7/D-b |
| C-39 | Any current or future CSV **export** sanitises `=`,`+`,`-`,`@`,TAB,CR,LF including after separators and quotes | Test G-08. No export exists yet — add the rule to the contract before someone adds the button |

### Destructive operations

| # | Control | Pass criterion |
|---|---|---|
| C-40 | Plan delete guard returns 409 with a **numeric** count | Carried over — `adminapi/exceptions.Conflict` preserves numbers. Test E-01 |
| C-41 | `ProtectedError` is caught and rendered as `PlanInUse` / `ProxyInUse`, never a 500 | Carried over in `adminapi/exceptions.py`. Tests E-02, I-02 |
| C-42 | The guard lives in `perform_destroy`/model layer so bulk paths inherit it | Test E-03 |
| C-43 | `bulk_delete` / `bulk_enable` cap `ids[]` and return a partial-failure shape | Tests I-01..I-03. **Contract omission** — `{deleted: n}` cannot express a partial failure |
| C-44 | Changes to `Plan.max_proxy_tier`, `max_chain_length`, `is_free_tier`, `is_active` emit a high-severity audit event with before/after | Tests D-02, D-03 — T3 |

### Transport, CORS, deployment

| # | Control | Pass criterion |
|---|---|---|
| C-45 | `CORS_ALLOWED_ORIGINS` is an explicit list; `CORS_ALLOW_ALL_ORIGINS` is `False` | Tests H-01..H-03. django-cors-headers' docs call `ALLOW_ALL_ORIGINS` dangerous and recommend an explicit list |
| C-46 | `CORS_ALLOW_CREDENTIALS` is `False` | Test H-04 |
| C-47 | Any origin regex is anchored `^...$` | Test H-03 |
| C-48 | `SessionAuthentication` is not in the admin API's authentication classes | Test H-06. Carried over — `DEFAULT_AUTHENTICATION_CLASSES` is JWT-only today; the risk is someone adding session auth "so the browsable API works" |
| C-49 | A deploy-time check fails the boot if `DEBUG` is true, `SECRET_KEY` is the default, or `JWT_SIGNING_KEY` equals `SECRET_KEY` in production | **Currently absent.** `core/checks.py` already demonstrates the pattern for `PROXY_CRED_KEY`; apply it here. Or wire `manage.py check --deploy` into the deploy pipeline. T6 |
| C-50 | `ALLOWED_HOSTS` does not contain `*` in production | |
| C-51 | HSTS / SSL redirect / secure cookies on | Carried over — gated on `not DEBUG`, which makes C-49 load-bearing |
| C-52 | The SPA sets a strict CSP with no `unsafe-inline`, and renders **no** API-sourced string through `dangerouslySetInnerHTML` — specifically `Content.body` and `UploadBatch.errors` | T11 |
| C-53 | Access token in memory only; refresh in `localStorage` is an accepted v1 risk **recorded with its expiry** (shorten per C-22) | Contract already states the first half |

### Credential handling at rest

| # | Control | Pass criterion |
|---|---|---|
| C-54 | `PROXY_CRED_KEY` present and valid or the process refuses to start | Carried over — `core/checks.py`, no plaintext fallback. This is the strongest control in the codebase; do not let the admin path bypass `EncryptedCharField` with a raw SQL update |
| C-55 | Admin writes go through the ORM field, not `.update()` with raw values | `queryset.update(password=...)` would write plaintext. Grep `bulk_enable`/`bulk_delete`/import paths for `.update(` touching credential columns |
| C-56 | P8 (KMS custody) remains tracked | `core/crypto.py:get_crypter()` is the documented seam |

---

## 6. Rate limiting and audit logging

### 6.1 The prerequisite

**Nothing in this section works until `CACHES` is configured.** DRF's `SimpleRateThrottle`
stores counters in Django's `default` cache. `core/settings.py` defines no `CACHES`, so Django
falls back to `LocMemCache` — per-process, non-shared, wiped on restart. With four gunicorn
workers the mobile credential throttle is not 60/hour, it is up to 240/hour, and an attacker who
triggers a deploy (or just waits for one) gets a fresh budget. Fix this first; everything below
is otherwise decorative.

### 6.2 Recommended scopes

| Scope | Endpoint | Suggested | Reasoning |
|---|---|---|---|
| `admin_login` | `POST /api/admin/auth/login/` | **5/min per IP** and **10/hour per submitted email** | Two keys, because one key protects the wrong thing. Per-IP alone lets a botnet through; per-email alone lets one attacker lock out every admin. Also mitigates `UPDATE_LAST_LOGIN=True`, which SimpleJWT's docs warn is a DoS vector and explicitly say to throttle |
| `admin_refresh` | `POST .../auth/refresh/` | 60/hour per user | |
| `admin_proxy_credentials` | `GET /api/admin/proxies/{id}/` | **120/hour per user**, plus a **hard daily ceiling (e.g. 400/day)** | 120/hour is roughly "an operator working through a support queue". It is **not** generous enough to dump a 50 000-row inventory — that would take ~17 days. The daily ceiling matters more than the hourly one: hourly limits are defeated by patience, daily limits are not. Do **not** reuse the mobile 60/hour (the SPA will hit it in normal use and someone will raise it to 10 000 to make the complaint stop) and do **not** fall through to the default `user` 120/min (= 7 200/hour, a full dump in 7 hours) |
| `admin_write` | all `POST`/`PATCH`/`PUT`/`DELETE` | 300/hour per user | Bounds accidental and malicious mass mutation |
| `admin_bulk` | `bulk_enable`, `bulk_delete` | 20/hour per user | Each call can touch 1 000 rows |
| `admin_csv_upload` | `upload_csv` | 10/hour per user | Each call is minutes of worker time |
| `admin` (default) | everything else | 600/hour per user | Dense tables poll; keep it loose enough not to fight the SPA |

**Structural recommendation beyond rate limits.** Move credentials off the detail serializer
entirely and behind an explicit `POST /api/admin/proxies/{id}/reveal/`. Three things improve at
once: the SPA stops burning the budget by merely navigating; every credential disclosure becomes
a deliberate operator act rather than a side effect of a page load; and the audit log stops being
a record of *page views* and starts being a record of *disclosures*. The cost is one extra click
in the SPA. The contract's current shape — credentials attached to the ordinary detail GET —
means the throttle number has to serve two conflicting masters (usability and anti-scrape), and
it will lose to usability.

### 6.3 Audit logging

Phase 1 has exactly one audit line, `logger.info("credential_access user=%s proxy=%s tier=%s")`
in `api/views.py`, going to a **console handler**. The draft `adminapi/views.py` follows the same
pattern (`_audit_credentials`, `admin_set_password`, `admin_bulk_delete`). That is the right
instinct and the wrong substrate.

**The regression nobody recorded:** Django Admin writes
`django.contrib.admin.models.LogEntry` for every add/change/delete, with actor, content type,
object id and a change message. D12 replaces that UI with an SPA that writes to stdout. The
project is **losing an audit trail it currently has**, and `memory.md`'s D12 consequences table
does not mention it. Add it there.

**Minimum bar.**

1. **A durable `AdminAuditLog` table**, not just log lines:
   `actor_id`, `actor_email` (denormalised — the user row may be deleted), `action`,
   `target_type`, `target_id`, `changes` (JSON, before/after for sensitive fields only),
   `ip`, `user_agent`, `request_id`, `created_at`. Append-only; no admin API endpoint that can
   delete it.
2. **Events that must be recorded, at minimum:**
   - every credential disclosure (proxy id, actor, ip) — including the `create`/`update`
     responses that echo credentials back, which the draft already handles
   - every `Plan` field change, with before/after (**T3 — the highest-value audit event in the
     system**)
   - every `is_staff` / `is_superuser` / `is_active` change
   - every `set_password` (actor and target)
   - every bulk operation, with the full id list
   - every CSV upload (batch id, row counts, filename)
   - every admin login **success and failure**, with IP
   - every subscription grant, extension or cancellation
3. **Ship it off-host.** A log an attacker with staff access can read, and a superuser can
   delete, is not evidence. Syslog/CloudWatch/Loki with retention measured in months.
4. **Structured, not formatted strings.** `logger.info("credential_access user=%s...")` is not
   queryable. JSON lines with a stable schema are.
5. **Three alerts worth having on day one** (the point of logging is detection, and these are
   the three threats that logging can actually catch):
   - credential disclosures by one actor exceeding ~50 in an hour, or any disclosure outside
     working hours → **T4**
   - **any** write to `Plan.max_proxy_tier`, `Plan.is_free_tier` or `Plan.max_chain_length` →
     **T3**. This should page a human. It is a handful of events per year and the one that costs
     the most
   - any `is_superuser` change, or any `set_password` where target ≠ actor → **T2**
6. **Do not log the credentials themselves.** Audit the *access*, never the value. Equally, keep
   them out of exception messages — `core/crypto.py` already raises without echoing the token;
   keep that property.

---

## 7. Out of scope for this phase

Named explicitly so the checklist is finite and so nobody mistakes silence for an oversight.

| Area | Why deferred | Revisit at |
|---|---|---|
| **Key custody / KMS (P8)** | Already tracked as a blocking pending decision. `PROXY_CRED_KEY` in the process environment defends dumps, backups and replicas (D11's stated scope) but not code execution on the app host | Before real paid credentials reach production |
| **Google Play receipt validation** | Phase 5. `Subscription.platform_txn_id` is unvalidated today, so subscription fraud via a forged token is not yet reachable — there is no verification path to bypass | Phase 5 |
| **The engine / client side** | Xray-core, `VpnService`, the TUN privilege layer, DNS leaks, `wintun.dll` (P5). Entirely separate trust domain | Phases 3–4 |
| **Mobile API redesign** | Ground Rule 1 freezes `/api/`. Its existing controls are reviewed here only for **carry-over**, except where the admin API creates a *new* path into it (T3 — in scope precisely because it crosses the boundary) |— |
| **Infrastructure** | OS hardening, container escape, network segmentation, WAF, DDoS, backup encryption and restore testing | Pre-production |
| **Supply chain** | Dependency CVE scanning, SBOM, lockfile integrity. `requirements.txt` is pinned, which is the prerequisite; scanning is a CI concern | CI setup |
| **Compliance** | GDPR/CCPA lawful basis, DSAR tooling, a privacy policy, the Play VPN declaration. A5's retention policy is flagged as a **risk** (T15) but writing it is not a security-architecture task | Phase 6 |
| **SPA-internal security beyond the API contract** | Dependency audit of the Vite/React tree, build pipeline integrity, subresource integrity. Only the token-handling and XSS-sink rules (C-52, C-53) are in scope here | SPA review |
| **Formal pen test** | This is a design-time threat model against a contract and an in-flight implementation. It does not replace testing the running service | After the SPA and API are both feature-complete |
| **Insider threat beyond logging** | Separation of duties, two-person rules for destructive operations, break-glass procedures. Correct answers for a larger team; premature for a single operator. The audit log is the proportionate control today | When staff headcount > ~3 |

---

## Appendix — controls that already exist and carry over

Worth stating plainly, because a threat model that only lists problems misrepresents the
codebase. These are good and should survive the admin API:

- **Credential encryption at rest with no plaintext fallback.** `core/crypto.py` +
  `core/fields.py` + `core/checks.py`. Fails the system check rather than silently writing
  plaintext. `MultiFernet` supports zero-downtime rotation and the `rotate_proxy_credentials`
  command exists. The admin API writes through the same fields, so this carries over for free —
  provided nothing uses `queryset.update()` on those columns (C-55).
- **`EncryptedCharField` refuses every lookup but `isnull`** rather than returning silently-empty
  results. A mistaken `search_fields = ("password",)` raises `FieldError` instead of quietly
  matching nothing. Fails loud, which is right.
- **Importer defaults `tier` to `premium`** when the column is absent — a mis-tagged import can
  never over-expose. Deliberate, documented, carries over.
- **Entitlement fallback is the *tightest* limit**, not the loosest. `ENTITLEMENT_FALLBACK`
  exists so a missing free plan cannot fail open.
- **`IsStaffUser` already checks `is_active`.** This is a non-obvious, correct call: a JWT
  outlives the account, and SimpleJWT's blacklist covers refresh tokens only, so without it a
  deactivated staff member keeps full admin access for up to 30 minutes.
- **Uniform admin login failure (`AdminLoginFailed`)** with its reasoning written down.
- **`Conflict` preserving numeric detail** through DRF's string coercion, so the SPA can render
  the subscription count.
- **`IdsFilterBackend` caps at 1 000 ids** and validates integers rather than passing strings to
  the ORM.
- **`DEFAULT_PERMISSION_CLASSES = IsAuthenticated`** — default-deny at the framework level. Note
  that for *this* app it is not sufficient (`IsAuthenticated` admits every end user), which is
  exactly why C-01 and C-02 exist.

---

### Sources consulted

- [OWASP — CSV Injection](https://owasp.org/www-community/attacks/CSV_Injection) and
  [OWASP WSTG — Testing for CSV Injection](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/07-Injection_Testing/21-Testing_for_CSV_Injection) — current trigger-character list (`=`,`+`,`-`,`@`,TAB,CR,LF), the separator/quote caveat, and the Excel re-save caveat
- [Symfony CVE-2021-41270](https://symfony.com/cve-2021-41270) — why the single-quote prefix replaced the tab prefix
- [djangorestframework-simplejwt settings reference](https://django-rest-framework-simplejwt.readthedocs.io/en/stable/settings.html) — `CHECK_REVOKE_TOKEN`/`REVOKE_TOKEN_CLAIM`, the `SIGNING_KEY`-independent-of-`SECRET_KEY` recommendation, `BLACKLIST_AFTER_ROTATION` requiring rotation plus the blacklist app, and the `UPDATE_LAST_LOGIN` DoS warning
- [DRF serializers guide](https://www.django-rest-framework.org/api-guide/serializers/) — `read_only_fields` is ignored for explicitly declared fields; `fields = "__all__"` is discouraged because new model fields are exposed automatically
- [django-cors-headers](https://pypi.org/project/django-cors-headers/) — `CORS_ALLOW_ALL_ORIGINS` is documented as dangerous; anchored regexes
- [DRF permissions](https://www.mintlify.com/encode/django-rest-framework/api/permissions) and the [Django forum thread on `get_permissions` and `@action`](https://forum.djangoproject.com/t/how-do-i-use-permission-classes-in-a-custom-method-of-a-viewset-in-django/17127) — `get_permissions` overrides keyed on `self.action` do not cover methods without an `@action` decorator, which is why the URLconf-walking startup check (C-02) is preferred over per-view discipline
