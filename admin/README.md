# Proxy Crack — admin SPA

The operations console. Replaces stock Django Admin (supersedes D7 in
`Project/memory.md`), and is built against `Project/admin-api-contract.md`
rather than against a running server.

Dark-first, Swiss/minimal, dense. Tokens come from
`design-system/proxy-crack/MASTER.md` and mirror the Flutter app's
`app/lib/core/theme/app_colors.dart`, so the two products read as one.

---

## Running it

```bash
npm install
npm run dev        # http://localhost:5173 — uses the in-memory mock
```

Sign in with `admin@proxycrack.io` / `proxycrack`. The login screen prints
this hint whenever the mock is active, so there is no hidden knowledge.

| Script | What it does |
|---|---|
| `npm run dev` | Dev server. `.env.development` sets `VITE_USE_MOCK_API=true`. |
| `npm run build` | `tsc -b && vite build`. `.env.production` points at the real API. |
| `npm run preview` | Serve the production build. |
| `npm run typecheck` | `tsc -b --force`, no emit. |
| `npm run check:contrast` | WCAG ratios for every colour pair the UI renders. |
| `npm run lint` | oxlint. |

### Switching from the mock to Django

One variable. No code change:

```bash
VITE_USE_MOCK_API=false
VITE_API_BASE_URL=http://127.0.0.1:8000     # no trailing slash
```

Set it in `.env.development.local` for day-to-day work, or just run
`npm run build`, which uses `.env.production` where it is already `false`.

The base URL is never hardcoded at a call site — `src/env.ts` reads it once
and the transport is the only thing that sees it. The fallback when the
variable is unset is `http://127.0.0.1:8000`.

---

## How the API layer is put together

```
src/api/
  types.ts          the contract, transcribed. No `any` anywhere in this dir.
  errors.ts         ApiError carrying {code, status, detail} + field mapping
  transport.ts      Transport interface + withAuthRefresh()
  httpTransport.ts  talks to Django (XHR for uploads, fetch for everything else)
  tokenStore.ts     access token in memory; refresh token in localStorage
  client.ts         the typed `api` object every screen calls
  mock/             in-memory implementation of the same contract
```

`Transport` is the only seam. `client.ts` picks `httpTransport` or
`mockTransport` from `env.useMockApi` and wraps either one in
`withAuthRefresh`. Nothing above that line knows which is in play.

The mock is loaded through a dynamic `import()`, so Rollup emits it as its own
chunk (~28 kB raw / ~10 kB gzip). With the mock off it is never fetched: it
costs a production build nothing, neither bytes on the critical path nor the
startup cost of generating 640 fixtures.

### Auth

- **Access token lives in a module variable and nowhere else.** Not
  localStorage, not sessionStorage, not a cookie.
- Refresh token is in `localStorage` under `pc.admin.refresh` — the accepted
  v1 trade-off.
- A 401 on an authenticated request triggers **one** refresh and **one**
  retry. Concurrent 401s share a single in-flight refresh instead of
  stampeding the endpoint. A failed refresh, or a retry that still 401s,
  clears both tokens and sends the operator to `/login`.
- Because the access token does not survive a reload, that path runs on
  *every* page load rather than only when a token happens to age out. It is
  exercised constantly, not in theory.
- `RequireAuth` renders a neutral splash while the boot probe runs, so a valid
  session never flashes a login screen. The shell is never mounted for an
  unauthenticated visitor.

### Proxy credentials

`GET /api/admin/proxies/{id}/` returns decrypted credentials, so it is treated
differently from every other read in the app:

- `useProxyCredentials` is deliberately **not** a TanStack Query hook. Query
  caches the *raw* response, and a `select` that strips secrets changes what
  the component sees, not what the cache holds.
- The detail drawer renders from the list row (which the contract guarantees
  carries no credentials) and calls the detail endpoint only when the operator
  presses **Reveal**, so each decryption is one deliberate, audit-logged
  request.
- Plaintext is held in component state, cleared when the proxy changes and on
  unmount. There is no copy-to-clipboard button: the clipboard outlives the
  panel and is readable by every other app on the machine.
- Edit forms never pre-fill credential fields — that would be a silent reveal.
  A blank field on `PATCH` is omitted so it keeps its stored value.

Verified in the browser: after revealing a password, neither it nor the
username appears in `localStorage`, `sessionStorage` or the URL. The only
storage key present is `pc.admin.refresh`.

---

## Dependencies, and why

| Package | Why it earns its place |
|---|---|
| `react-router-dom` | Routing, and list state lives in the URL so a filtered view is shareable and survives a reload. |
| `@tanstack/react-query` | Server cache, background refetch, request dedupe, per-surface loading/error state. Replaces a large amount of hand-rolled code. |
| `@tanstack/react-virtual` | ~5 kB. Page sizes go to 200 on the proxy and usage tables; windowing holds the DOM at ~25 rows. |
| `lucide-react` | Icon set. No emoji as icons. |

Everything else is hand-written: no UI kit, no CSS framework, no form
library, no chart library. Styling is plain CSS over custom properties
(`src/styles/`), which keeps the Swiss grid exact and ships no runtime.

Charts are inline SVG (`src/components/charts/`) — a chart library would have
been the single largest dependency in the project for two small plots.

---

## Design notes

### Contrast

`npm run check:contrast` measures all 48 foreground/background pairs the UI
actually renders, compositing alpha tints over their real backdrop first.
All pass: AA 4.5:1 for text, 3:1 for focus rings, status marks and series
colours. The script fails the command on a regression.

**The trap, stated so nobody walks back into it:** `#DC2626` measures
**3.70:1** as text on `#0F172A` and fails AA. It is a **fill-only** token —
white on it is 4.83:1. Red text and red icons use `#F87171` (6.45:1).

### Status is never carried by colour alone

Every alive/dead, enabled/disabled, active/expired and published/draft
indicator renders an **icon plus a text label**. Green/red on this surface
separates by dE ~4 under deuteranopia against ~34 for normal vision — and
proxy health is the signal an operator acts on most. Colour is reinforcement;
the icon and the word carry the meaning. Latency is a number first, tinted
second.

### Charts

- Protocol distribution: horizontal bars, sorted by magnitude, direct-labelled,
  using the validated categorical palette (`ss #3987E5`, `socks5 #D95926`,
  `http #9085E9`) in fixed assignment, never cycled. Green is excluded because
  `#22C55E` means *connected*, not *a protocol*.
- Proxy health and subscription mix: stat tiles with proportion bars. Two
  values do not need a chart.
- Bandwidth: one chart, two series, **one shared y scale**. Never a dual axis.
- Hover is standard, not optional: crosshair, per-series markers and a tooltip,
  with a hit area larger than the marks. Each chart also renders a
  visually-hidden data table, so it is not a dead end for a screen reader.

### Focus

One global rule, never removed:

```css
:focus { outline: none }
:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px }
```

Table rows are not focusable. A focusable `<tr>` announces poorly and
duplicates every action in the row; instead the first cell is a real button
and the row actions are real buttons, so a keyboard user tabs to meaningful
targets. Rows carry `scroll-margin-top` so a focused control is never scrolled
underneath the sticky column header.

### Tables

Every table is bounded (`max-height`) and scrolls internally, so the toolbar,
bulk bar and pagination stay on screen while rows move. That also makes the
sticky header work: `overflow: auto` makes the wrapper — not the viewport —
the sticky scrollport, so the header sticks at `top: 0` inside it.

Gridlines use `#475569` rather than the `rgba(255,255,255,0.08)` card-border
token. A dense table needs that structure; a card edge does not.

Numerics (IPs, ports, latency, byte counts) are set in JetBrains Mono with
`font-variant-numeric: tabular-nums lining-nums` so columns align and scan.

### Responsive

| Width | Layout |
|---|---|
| ≥ 1180px | Full sidebar with labels |
| 768–1179px | Icon rail (tablet — the brief's floor) |
| < 768px | Off-canvas drawer behind a menu button, with a scrim |

---

## Notes for the Django side

Places where the contract named a field but not its value domain, or where the
SPA had to pick a convention. Each is a one-line change here if the backend
lands somewhere else — the marker `@contract-assumption` in
`src/api/types.ts` flags them all.

1. **`GET /api/admin/usage-logs/` date range parameter names.** The contract
   says "date range on `session_start`" without naming the parameters. The SPA
   sends `?session_start_after=` / `?session_start_before=` (ISO-8601), the
   django-filter `DateFromToRangeFilter` default. Change
   `UsageLogListParams` in `src/api/client.ts` if you use different suffixes.

2. **`/stats/` has no daily series.** It gives 7-day *totals*
   (`bytes_up_7d`, `sessions_7d`, `new_7d`) but no per-day breakdown, so the
   dashboard's bandwidth and signup charts derive their shape client-side by
   bucketing `/usage-logs/` and `/users/`. That works at this scale and the
   cards say on screen when the sample was truncated, but it is the wrong
   shape for a real dataset — it costs up to 3 extra list requests on
   dashboard load and cannot see past `page_size × 3` rows.
   **Worth adding to the contract:** `usage.daily_7d: [{date, bytes_up,
   bytes_down, sessions}]` and `users.daily_14d: [{date, count}]`. The SPA
   would then drop `src/pages/useDerivedSeries.ts` entirely.

3. **`user.subscription` nested summary members** are unspecified. The SPA
   reads `{id, plan_code, plan_name, status, expires_at}`.

4. **Value domains assumed:** `tier` ∈ `free|premium`; subscription `status` ∈
   `active|expired|cancelled|pending` (the last two inferred from the cancel
   action); `platform` ∈ `play|stripe|manual`; `content.kind` is treated as an
   open string, with `announcement|faq|legal|tip` offered as suggestions.

5. **`plan.price`** is typed as a string (DRF's default
   `COERCE_DECIMAL_TO_STRING`). `toNumber()` in `src/lib/format.ts` accepts a
   number too, so flipping that setting will not break the UI.

6. **Nested reads, flat writes.** Subscriptions are read with nested `user`
   and `plan` objects and written with integer ids (`SubscriptionWrite`).

7. **`DELETE /api/admin/users/{id}/`** — the SPA disables the delete control
   for the signed-in operator's own account and the mock returns 409. Worth
   enforcing server-side; the client check is UI only.

8. **Error envelope.** `{"error": {"code", "status", "detail"}}` where
   `detail` is a string for request-level failures or a `{field: [messages]}`
   map for serializer rejections — the field map is what drives inline
   validation. `toApiError` also tolerates bare DRF shapes (`{detail: "..."}`
   and a plain field map) for views that bypass the project handler.

Client-side role checks (`is_staff`, `is_superuser`) hide UI only. The server
decides. Every admin endpoint must enforce `IsAuthenticated AND is_staff` and
return **403** to a valid end-user token.

---

## Verification

| Check | Result |
|---|---|
| `npx tsc -b --force` | clean, strict mode on |
| `npm run build` | succeeds |
| `npm run check:contrast` | 48/48 pairs pass |
| Browser | driven with Playwright at 1440 / 1024 / 760 px |

`strict` is **not** in the Vite react-ts scaffold's `tsconfig.app.json`; it is
set explicitly in this project, along with `noImplicitOverride`,
`noImplicitReturns` and the scaffold's `noUnusedLocals` / `noUnusedParameters`.

Screenshots of each verified screen are in `docs/screenshots/`.
