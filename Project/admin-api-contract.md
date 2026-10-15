# Admin API Contract

The contract between the Django admin API (`backend/adminapi/`) and the React SPA
(`admin/`). Both are built against this document so they can be developed in parallel.

**Decided:** 2026-10-15 (supersedes D7 — stock Django Admin is being replaced)

---

## Ground rules

1. **The existing mobile API at `/api/` must not change.** It is entitlement-scoped by design —
   a free user sees a filtered subset. The admin API is **additive**, mounted at `/api/admin/`.
2. **Every admin endpoint is staff-only.** `IsAuthenticated AND is_staff`. A valid end-user JWT
   must receive **403**, not data. This is the single most important rule here — the admin API
   is unscoped, so a leak is total.
3. **No entitlement filtering** on admin endpoints. Admins see everything.
4. Django Admin stays mounted at `/admin/` as a superuser fallback. It is not deleted.

---

## Auth

```
POST /api/admin/auth/login/      {email, password} -> {access, refresh, user}
POST /api/admin/auth/refresh/    {refresh}         -> {access}
POST /api/admin/auth/logout/     {refresh}         -> 204  (blacklist)
GET  /api/admin/auth/me/                           -> {id, email, first_name, last_name,
                                                       is_staff, is_superuser}
```

`login` **must reject non-staff with 403** even when the password is correct, and must not
reveal whether the account exists. Returns the same JWT pair shape as the mobile API.

---

## Conventions

- **Pagination:** `?page=`, `?page_size=` (default 25, max 200) →
  `{count, next, previous, results[]}`
- **Search:** `?search=` (per-resource fields listed below)
- **Ordering:** `?ordering=field` / `?ordering=-field`
- **Filtering:** django-filter, per-resource fields listed below
- **Errors:** reuse the existing envelope — `{"error": {"code", "status", "detail"}}`
- **Timestamps:** ISO-8601 UTC
- All list endpoints support `?ids=1,2,3` for bulk selection round-trips

---

## Resources

### `GET /api/admin/stats/` — dashboard

```json
{
  "users":         {"total": 0, "staff": 0, "new_7d": 0},
  "subscriptions": {"active": 0, "expired": 0, "free_tier": 0, "paid": 0},
  "proxies":       {"total": 0, "alive": 0, "dead": 0, "free": 0, "premium": 0,
                    "by_protocol": {"ss": 0, "socks5": 0, "http": 0}},
  "usage":         {"bytes_up_7d": 0, "bytes_down_7d": 0, "sessions_7d": 0},
  "uploads":       {"last_batch_at": null, "last_batch_rows": 0}
}
```

### `/api/admin/users/` — full CRUD
- fields: `id, email, first_name, last_name, is_active, is_staff, is_superuser, date_joined, last_login, subscription (nested summary)`
- search: `email, first_name, last_name` · filter: `is_active, is_staff` · order: `date_joined, email, last_login`
- `POST /api/admin/users/{id}/set_password/` `{password}` → 204
- **Never return password hashes.**

### `/api/admin/plans/` — full CRUD
- fields: `id, code, name, price, duration_days, max_chain_length, max_proxy_tier, max_devices, bandwidth_limit_bytes, is_free_tier, is_active, sort_order, play_product_id`
- **Guard:** refuse to delete a plan with active subscriptions → 409 with the count.

### `/api/admin/subscriptions/` — full CRUD
- fields: `id, user (nested: id, email), plan (nested: id, code, name), started_at, expires_at, status, platform, platform_txn_id, last_verified_at`
- filter: `status, plan, user` · search: `user__email` · order: `started_at, expires_at`
- `POST /api/admin/subscriptions/{id}/cancel/` → 200

### `/api/admin/proxies/` — full CRUD
- list fields: `id, protocol, host, port, country, city, tier, is_alive, is_enabled, latency_ms, last_checked, requires_credentials, upload_batch`
- detail adds: `username, password, ss_method` (decrypted)
- filter: `protocol, tier, country, is_alive, is_enabled` · search: `host, country, city` · order: `latency_ms, last_checked, host`
- bulk: `POST /api/admin/proxies/bulk_enable/` `{ids[], is_enabled}` → `{updated: n}`
- bulk: `POST /api/admin/proxies/bulk_delete/` `{ids[]}` → `{deleted: n}`

> ⚠️ **Credentials are decrypted on the detail endpoint only**, never in list responses, and only
> for staff. Keep the existing audit log line firing. The SPA must not cache them.

### `POST /api/admin/proxies/upload_csv/` — bulk import
- `multipart/form-data`, field `file`
- → `201 {batch_id, row_count, created_count, updated_count, fail_count, errors: [{line, reason}]}`
- Reuse `proxies/importers.py` — do not reimplement the parsing.

### `/api/admin/upload-batches/` — read-only
- fields: `id, filename, uploaded_by (email), row_count, success_count, created_count, updated_count, fail_count, errors, created_at`

### `/api/admin/content/` — full CRUD
- fields: `id, key, kind, title, body, priority, is_published, created_at, updated_at`

### `/api/admin/usage-logs/` — read-only
- fields: `id, user (id, email), bytes_up, bytes_down, session_start, session_end`
- filter: `user`, date range on `session_start`

### `/api/admin/chains/` — read-only
- fields: `id, user (id, email), name, hops: [{position, proxy: {id, protocol, host, country}}], created_at`

---

## SPA requirements

**Stack:** Vite + React + TypeScript. Not Next.js — this consumes a separate API and needs no SSR.

**Design tokens** (from `design-system/proxy-crack/MASTER.md`, same as the mobile app):

```
background  #0F172A    card        #192134    primary   #1E3A5F
secondary   #334155    accent      #22C55E    border    rgba(255,255,255,0.08)
mutedFg     #94A3B8    destructive #DC2626 (fill only — 3.70:1, FAILS as text)
destructiveText #F87171 (6.45:1 ✅)    subtleFg #CBD5E1    warning #FBBF24
font: Inter
```

Dark-first, Swiss/minimal, dense tables. Same visual language as the mobile app.

**Must implement:**
- JWT login with refresh-on-401 and a single retry, then redirect to login
- **Access token in memory only.** Refresh token in `localStorage` is acceptable for v1; never
  put the access token there.
- Route guard — unauthenticated users never see the shell
- Sidebar nav, dense data tables with sort/filter/search/pagination
- Create + edit forms with inline validation surfaced from the API error envelope
- CSV upload with drag-drop, progress, and a per-line error report
- Dashboard from `/api/admin/stats/`
- Loading, empty and error states on every async surface
- Confirm dialogs on every destructive action
- Keyboard accessible, visible focus rings, 4.5:1 contrast minimum

**Must not:**
- Persist decrypted proxy credentials anywhere
- Trust client-side role checks for anything but hiding UI — the server decides
