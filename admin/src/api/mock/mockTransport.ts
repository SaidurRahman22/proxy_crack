/**
 * In-memory implementation of `Project/admin-api-contract.md`.
 *
 * It exists so the SPA can be built, demoed and reviewed while the Django API
 * is being written in parallel against the same document. It implements the
 * contract's *semantics*, not just its shapes: pagination, `?search=`,
 * `?ordering=`, django-filter fields, `?ids=`, the `{error:{code,status,detail}}`
 * envelope, the staff-only 403 on login, the 409 guard on deleting a plan with
 * active subscriptions, and access tokens that genuinely expire so the
 * refresh-on-401 path runs rather than merely existing.
 *
 * Swapping it for the real API is `VITE_USE_MOCK_API=false`.
 */

import { env } from '@/env'

import { ApiError } from '../errors'
import { tokenStore } from '../tokenStore'
import type { QueryParams, RequestSpec, Transport } from '../transport'
import type {
  AdminUser,
  BulkDeleteResponse,
  BulkEnableResponse,
  Chain,
  ContentItem,
  CsvUploadResponse,
  LoginResponse,
  Paginated,
  Plan,
  Protocol,
  ProxyDetail,
  ProxyListItem,
  ProxyTier,
  RefreshResponse,
  RowError,
  Stats,
  Subscription,
  SubscriptionStatus,
  UploadBatch,
  UsageLog,
  User,
} from '../types'
import { MOCK_NON_STAFF_EMAIL, MOCK_STAFF_EMAIL, MOCK_STAFF_PASSWORD } from './credentials'
import {
  chains,
  content,
  NOW,
  plans,
  proxies,
  subscriptions,
  uploadBatches,
  usageLogs,
  users,
} from './data'

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

function fail(
  status: number,
  code: string,
  detail: ApiError['detail'],
  extra: Record<string, unknown> = {},
): never {
  throw new ApiError(status, code, detail, extra)
}

type Primitive = string | number | boolean | null

function str(query: QueryParams | undefined, key: string): string | undefined {
  const value = query?.[key]
  if (value === undefined || value === null || value === '') return undefined
  return Array.isArray(value) ? value.join(',') : String(value)
}

function num(query: QueryParams | undefined, key: string): number | undefined {
  const raw = str(query, key)
  if (raw === undefined) return undefined
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : undefined
}

function bool(query: QueryParams | undefined, key: string): boolean | undefined {
  const raw = str(query, key)
  if (raw === undefined) return undefined
  return raw === 'true' || raw === '1' || raw === 'True'
}

function idSet(query: QueryParams | undefined): Set<number> | undefined {
  const raw = str(query, 'ids')
  if (raw === undefined) return undefined
  const ids = raw.split(',').map(Number).filter(Number.isFinite)
  return ids.length > 0 ? new Set(ids) : undefined
}

/** Resolve `a.b.c` against a record, for `?ordering=` and `?search=` paths. */
function path(record: unknown, dotted: string): Primitive | undefined {
  let current: unknown = record
  for (const part of dotted.split(/[.]|__/)) {
    if (typeof current !== 'object' || current === null) return undefined
    current = (current as Record<string, unknown>)[part]
  }
  if (current === null) return null
  if (typeof current === 'string' || typeof current === 'number' || typeof current === 'boolean') {
    return current
  }
  return undefined
}

function compare(a: Primitive | undefined, b: Primitive | undefined): number {
  // Nulls sort last regardless of direction — a missing latency is not "fast".
  if (a === null || a === undefined) return b === null || b === undefined ? 0 : 1
  if (b === null || b === undefined) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), undefined, { numeric: true })
}

function applyOrdering<T>(items: T[], ordering: string | undefined, fallback?: string): T[] {
  const spec = ordering ?? fallback
  if (!spec) return items
  const desc = spec.startsWith('-')
  const field = desc ? spec.slice(1) : spec
  const sorted = [...items].sort((a, b) => compare(path(a, field), path(b, field)))
  return desc ? sorted.reverse() : sorted
}

function applySearch<T>(items: T[], term: string | undefined, fields: string[]): T[] {
  if (!term) return items
  const needle = term.trim().toLowerCase()
  if (needle === '') return items
  return items.filter((item) =>
    fields.some((field) => String(path(item, field) ?? '').toLowerCase().includes(needle)),
  )
}

function paginate<T>(items: T[], query: QueryParams | undefined): Paginated<T> {
  const page = Math.max(1, num(query, 'page') ?? 1)
  const pageSize = Math.min(200, Math.max(1, num(query, 'page_size') ?? 25))
  const start = (page - 1) * pageSize
  const slice = items.slice(start, start + pageSize)
  return {
    count: items.length,
    next: start + pageSize < items.length ? `?page=${page + 1}` : null,
    previous: page > 1 ? `?page=${page - 1}` : null,
    results: slice,
  }
}

function nextId(items: Array<{ id: number }>): number {
  return items.reduce((max, item) => Math.max(max, item.id), 0) + 1
}

function asRecord(body: unknown): Record<string, unknown> {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    fail(400, 'invalid_body', 'Expected a JSON object.')
  }
  return body as Record<string, unknown>
}

function requireString(record: Record<string, unknown>, field: string): string {
  const value = record[field]
  if (typeof value !== 'string' || value.trim() === '') {
    fail(400, 'validation_error', { [field]: ['This field is required.'] })
  }
  return value
}

function numberIds(body: unknown): number[] {
  const record = asRecord(body)
  const raw = record.ids
  if (!Array.isArray(raw)) fail(400, 'validation_error', { ids: ['Expected a list of ids.'] })
  const ids = raw.filter((v): v is number => typeof v === 'number')
  if (ids.length === 0) fail(400, 'validation_error', { ids: ['Select at least one proxy.'] })
  return ids
}

/* ------------------------------------------------------------------ *
 * Session
 * ------------------------------------------------------------------ */

/**
 * Access tokens expire after 2 minutes so the refresh-on-401 retry is a live
 * code path in mock mode, not dead code waiting on the real backend.
 */
const ACCESS_TTL_MS = 120_000
const blacklist = new Set<string>()

function issueAccess(userId: number): string {
  return `mock.access.${userId}.${Date.now() + ACCESS_TTL_MS}`
}

function issueRefresh(userId: number): string {
  return `mock.refresh.${userId}.${Date.now() + 7 * 86400000}`
}

function readToken(token: string, kind: 'access' | 'refresh'): number {
  const parts = token.split('.')
  if (parts.length !== 4 || parts[0] !== 'mock' || parts[1] !== kind) {
    fail(401, 'token_not_valid', 'Token is invalid.')
  }
  if (blacklist.has(token)) fail(401, 'token_not_valid', 'Token has been blacklisted.')
  const expiry = Number(parts[3])
  if (!Number.isFinite(expiry) || Date.now() > expiry) {
    fail(401, 'token_not_valid', 'Token is expired.')
  }
  return Number(parts[2])
}

function requireStaff(spec: RequestSpec): User {
  if (spec.auth === false) fail(500, 'mock_error', 'Unauthenticated request to a staff endpoint.')
  const token = tokenStore.getAccess()
  if (!token) fail(401, 'not_authenticated', 'Authentication credentials were not provided.')
  const userId = readToken(token, 'access')
  const user = users.find((u) => u.id === userId)
  // Rule 2 of the contract: staff-only, and a valid end-user token gets 403.
  if (!user) fail(401, 'not_authenticated', 'Unknown account.')
  if (!user.is_staff) fail(403, 'permission_denied', 'Staff access is required.')
  return user
}

function toAdminUser(user: User): AdminUser {
  return {
    id: user.id,
    email: user.email,
    first_name: user.first_name,
    last_name: user.last_name,
    is_staff: user.is_staff,
    is_superuser: user.is_superuser,
  }
}

/* ------------------------------------------------------------------ *
 * Projections
 * ------------------------------------------------------------------ */

/** The contract forbids credentials in list responses. Strip them here. */
function toListItem(proxy: ProxyDetail): ProxyListItem {
  const { username: _u, password: _p, ss_method: _m, ...rest } = proxy
  return rest
}

function computeStats(): Stats {
  const sevenDaysAgo = new Date(NOW.getTime() - 7 * 86400000).toISOString()
  const recentLogs = usageLogs.filter((log) => log.session_start >= sevenDaysAgo)
  const latestBatch = uploadBatches[0]

  return {
    users: {
      total: users.length,
      staff: users.filter((u) => u.is_staff).length,
      new_7d: users.filter((u) => u.date_joined >= sevenDaysAgo).length,
    },
    subscriptions: {
      active: subscriptions.filter((s) => s.status === 'active').length,
      expired: subscriptions.filter((s) => s.status === 'expired').length,
      free_tier: subscriptions.filter((s) => s.plan.code === 'free').length,
      paid: subscriptions.filter((s) => s.plan.code !== 'free').length,
    },
    proxies: {
      total: proxies.length,
      alive: proxies.filter((p) => p.is_alive).length,
      dead: proxies.filter((p) => !p.is_alive).length,
      free: proxies.filter((p) => p.tier === 'free').length,
      premium: proxies.filter((p) => p.tier === 'premium').length,
      by_protocol: {
        ss: proxies.filter((p) => p.protocol === 'ss').length,
        socks5: proxies.filter((p) => p.protocol === 'socks5').length,
        http: proxies.filter((p) => p.protocol === 'http').length,
      },
    },
    usage: {
      bytes_up_7d: recentLogs.reduce((sum, log) => sum + log.bytes_up, 0),
      bytes_down_7d: recentLogs.reduce((sum, log) => sum + log.bytes_down, 0),
      sessions_7d: recentLogs.length,
    },
    uploads: {
      last_batch_at: latestBatch?.created_at ?? null,
      last_batch_rows: latestBatch?.row_count ?? 0,
    },
  }
}

/* ------------------------------------------------------------------ *
 * CSV import — mirrors what proxies/importers.py is expected to accept
 * ------------------------------------------------------------------ */

const CSV_COLUMNS = ['protocol', 'host', 'port', 'country', 'city', 'tier', 'username', 'password', 'ss_method']
const VALID_PROTOCOLS: Protocol[] = ['ss', 'socks5', 'http']
const VALID_TIERS: ProxyTier[] = ['free', 'premium']

async function importCsv(file: File): Promise<CsvUploadResponse> {
  const text = await file.text()
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== '')
  const errors: RowError[] = []

  if (lines.length === 0) {
    fail(400, 'empty_file', { file: ['The file is empty.'] })
  }

  const header = lines[0]!.split(',').map((h) => h.trim().toLowerCase())
  const missing = ['protocol', 'host', 'port', 'country'].filter((c) => !header.includes(c))
  if (missing.length > 0) {
    fail(400, 'bad_header', {
      file: [
        `Missing required column${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}. ` +
          `Expected header: ${CSV_COLUMNS.join(',')}`,
      ],
    })
  }

  const column = (cells: string[], name: string): string => {
    const index = header.indexOf(name)
    return index === -1 ? '' : (cells[index] ?? '').trim()
  }

  let created = 0
  let updated = 0
  const seen = new Set<string>()

  for (let i = 1; i < lines.length; i += 1) {
    const lineNumber = i + 1
    const cells = lines[i]!.split(',')
    const protocol = column(cells, 'protocol').toLowerCase()
    const host = column(cells, 'host')
    const portRaw = column(cells, 'port')
    const countryRaw = column(cells, 'country').toUpperCase()
    const tierRaw = column(cells, 'tier').toLowerCase()
    const ssMethod = column(cells, 'ss_method')

    if (!VALID_PROTOCOLS.includes(protocol as Protocol)) {
      errors.push({
        line: lineNumber,
        reason: `unknown protocol "${protocol || '(blank)'}" — expected one of ${VALID_PROTOCOLS.join(', ')}`,
      })
      continue
    }
    if (host === '') {
      errors.push({ line: lineNumber, reason: 'host is required' })
      continue
    }
    const port = Number(portRaw)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      errors.push({ line: lineNumber, reason: `port "${portRaw}" must be an integer between 1 and 65535` })
      continue
    }
    if (countryRaw.length !== 2) {
      errors.push({ line: lineNumber, reason: `country "${countryRaw}" must be a 2-letter ISO code` })
      continue
    }
    if (tierRaw !== '' && !VALID_TIERS.includes(tierRaw as ProxyTier)) {
      errors.push({ line: lineNumber, reason: `unknown tier "${tierRaw}" — expected free or premium` })
      continue
    }
    if (protocol === 'ss' && ssMethod === '') {
      errors.push({ line: lineNumber, reason: 'ss_method is required when protocol is ss' })
      continue
    }
    const key = `${host}:${port}`
    if (seen.has(key)) {
      errors.push({ line: lineNumber, reason: `duplicate ${key} in this file` })
      continue
    }
    seen.add(key)

    const existing = proxies.find((p) => p.host === host && p.port === port)
    const record: Omit<ProxyDetail, 'id'> = {
      protocol: protocol as Protocol,
      host,
      port,
      country: countryRaw,
      city: column(cells, 'city') || null,
      tier: (tierRaw === '' ? 'premium' : tierRaw) as ProxyTier,
      is_alive: existing?.is_alive ?? true,
      is_enabled: existing?.is_enabled ?? true,
      latency_ms: existing?.latency_ms ?? null,
      last_checked: existing?.last_checked ?? null,
      requires_credentials: column(cells, 'password') !== '' || protocol === 'ss',
      upload_batch: null,
      username: column(cells, 'username') || null,
      password: column(cells, 'password') || null,
      ss_method: ssMethod || null,
    }

    if (existing) {
      Object.assign(existing, record, { id: existing.id })
      updated += 1
    } else {
      proxies.push({ ...record, id: nextId(proxies) })
      created += 1
    }
  }

  const rowCount = lines.length - 1
  const batch: UploadBatch = {
    id: nextId(uploadBatches),
    filename: file.name,
    uploaded_by: MOCK_STAFF_EMAIL,
    row_count: rowCount,
    success_count: created + updated,
    created_count: created,
    updated_count: updated,
    fail_count: errors.length,
    errors,
    created_at: new Date().toISOString(),
  }
  uploadBatches.unshift(batch)
  for (const proxy of proxies) {
    if (proxy.upload_batch === null && proxy.id > PROXY_SEED_COUNT) proxy.upload_batch = batch.id
  }

  return {
    batch_id: batch.id,
    row_count: batch.row_count,
    created_count: batch.created_count,
    updated_count: batch.updated_count,
    fail_count: batch.fail_count,
    errors: batch.errors,
  }
}

const PROXY_SEED_COUNT = proxies.length

/* ------------------------------------------------------------------ *
 * Router
 * ------------------------------------------------------------------ */

type Handler = (spec: RequestSpec, params: string[]) => unknown | Promise<unknown>

interface Route {
  method: RequestSpec['method']
  pattern: RegExp
  handle: Handler
}

const ID = String.raw`(\d+)`

function route(method: RequestSpec['method'], pattern: string, handle: Handler): Route {
  return { method, pattern: new RegExp(`^${pattern}$`), handle }
}

/* --- generic CRUD factory ---------------------------------------- */

interface ListConfig<T> {
  items: T[]
  searchFields: string[]
  defaultOrdering: string
  filter?: (item: T, query: QueryParams | undefined) => boolean
  /** Adds server-computed read-only fields to each row of the page. */
  decorate?: (item: T) => T
}

function listHandler<T extends { id: number }>(config: ListConfig<T>): Handler {
  return (spec) => {
    requireStaff(spec)
    const ids = idSet(spec.query)
    let items = config.items
    if (ids) items = items.filter((item) => ids.has(item.id))
    if (config.filter) items = items.filter((item) => config.filter!(item, spec.query))
    items = applySearch(items, str(spec.query, 'search'), config.searchFields)
    items = applyOrdering(items, str(spec.query, 'ordering'), config.defaultOrdering)
    const page = paginate(items, spec.query)
    return config.decorate ? { ...page, results: page.results.map(config.decorate) } : page
  }
}

function findOr404<T extends { id: number }>(items: T[], raw: string | undefined, label: string): T {
  const id = Number(raw)
  const found = items.find((item) => item.id === id)
  if (!found) fail(404, 'not_found', `No ${label} with id ${raw}.`)
  return found
}

const routes: Route[] = [
  /* ---- Auth -------------------------------------------------------- */
  route('POST', '/api/admin/auth/login/', (spec) => {
    const record = asRecord(spec.body)
    const email = requireString(record, 'email').trim().toLowerCase()
    const password = requireString(record, 'password')

    /*
     * EVERY login failure answers 403 with a byte-identical body -- unknown
     * email, wrong password, and correct-password-but-not-staff are
     * indistinguishable, so the endpoint cannot be used to enumerate accounts.
     * MOCK_NON_STAFF_EMAIL exists to prove the third case is handled no
     * differently from the first two.
     */
    void MOCK_NON_STAFF_EMAIL
    if (email !== MOCK_STAFF_EMAIL || password !== MOCK_STAFF_PASSWORD) {
      fail(403, 'invalid_credentials', 'Unable to sign in with those credentials.')
    }

    const user = users.find((u) => u.email === MOCK_STAFF_EMAIL)!
    const response: LoginResponse = {
      access: issueAccess(user.id),
      refresh: issueRefresh(user.id),
      user: toAdminUser(user),
    }
    return response
  }),

  route('POST', '/api/admin/auth/refresh/', (spec) => {
    const record = asRecord(spec.body)
    const refresh = requireString(record, 'refresh')
    const userId = readToken(refresh, 'refresh')
    // ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION: the submitted token is
    // dead from here on and a replacement is issued. A client that keeps the
    // old one is logged out at its next refresh -- which is exactly what this
    // models, so the SPA's token handling is exercised rather than assumed.
    blacklist.add(refresh)
    const response: RefreshResponse = {
      access: issueAccess(userId),
      refresh: issueRefresh(userId),
    }
    return response
  }),

  route('POST', '/api/admin/auth/logout/', (spec) => {
    const record = asRecord(spec.body)
    blacklist.add(requireString(record, 'refresh'))
    return null
  }),

  route('GET', '/api/admin/auth/me/', (spec) => toAdminUser(requireStaff(spec))),

  /* ---- Dashboard --------------------------------------------------- */
  route('GET', '/api/admin/stats/', (spec) => {
    requireStaff(spec)
    return computeStats()
  }),

  /* ---- Users ------------------------------------------------------- */
  route(
    'GET',
    '/api/admin/users/',
    listHandler<User>({
      items: users,
      searchFields: ['email', 'first_name', 'last_name'],
      defaultOrdering: '-date_joined',
      filter: (user, query) => {
        const isActive = bool(query, 'is_active')
        const isStaff = bool(query, 'is_staff')
        if (isActive !== undefined && user.is_active !== isActive) return false
        if (isStaff !== undefined && user.is_staff !== isStaff) return false
        return true
      },
    }),
  ),
  route('GET', `/api/admin/users/${ID}/`, (spec, params) => {
    requireStaff(spec)
    return findOr404(users, params[0], 'user')
  }),
  route('POST', '/api/admin/users/', (spec) => {
    requireStaff(spec)
    const record = asRecord(spec.body)
    const email = requireString(record, 'email').trim().toLowerCase()
    if (!email.includes('@')) {
      fail(400, 'validation_error', { email: ['Enter a valid email address.'] })
    }
    if (users.some((u) => u.email.toLowerCase() === email)) {
      fail(400, 'validation_error', { email: ['A user with that email already exists.'] })
    }
    const password = typeof record.password === 'string' ? record.password : ''
    if (password.length < 8) {
      fail(400, 'validation_error', { password: ['Password must be at least 8 characters.'] })
    }
    const created: User = {
      id: nextId(users),
      email,
      first_name: typeof record.first_name === 'string' ? record.first_name : '',
      last_name: typeof record.last_name === 'string' ? record.last_name : '',
      is_active: record.is_active !== false,
      is_staff: record.is_staff === true,
      is_superuser: record.is_superuser === true,
      date_joined: new Date().toISOString(),
      last_login: null,
      subscription: null,
    }
    users.unshift(created)
    return created
  }),
  route('PATCH', `/api/admin/users/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const user = findOr404(users, params[0], 'user')
    const record = asRecord(spec.body)
    if (typeof record.email === 'string') {
      const email = record.email.trim().toLowerCase()
      if (!email.includes('@')) fail(400, 'validation_error', { email: ['Enter a valid email address.'] })
      if (users.some((u) => u.id !== user.id && u.email.toLowerCase() === email)) {
        fail(400, 'validation_error', { email: ['A user with that email already exists.'] })
      }
      user.email = email
    }
    if (typeof record.first_name === 'string') user.first_name = record.first_name
    if (typeof record.last_name === 'string') user.last_name = record.last_name
    if (typeof record.is_active === 'boolean') user.is_active = record.is_active
    if (typeof record.is_staff === 'boolean') user.is_staff = record.is_staff
    if (typeof record.is_superuser === 'boolean') user.is_superuser = record.is_superuser
    return user
  }),
  route('DELETE', `/api/admin/users/${ID}/`, (spec, params) => {
    const actor = requireStaff(spec)
    const user = findOr404(users, params[0], 'user')
    if (user.id === actor.id) {
      fail(409, 'cannot_delete_self', 'You cannot delete the account you are signed in with.')
    }
    users.splice(users.indexOf(user), 1)
    return null
  }),
  route('POST', `/api/admin/users/${ID}/set_password/`, (spec, params) => {
    requireStaff(spec)
    findOr404(users, params[0], 'user')
    const record = asRecord(spec.body)
    const password = requireString(record, 'password')
    if (password.length < 8) {
      fail(400, 'validation_error', { password: ['Password must be at least 8 characters.'] })
    }
    return null
  }),

  /* ---- Plans ------------------------------------------------------- */
  route(
    'GET',
    '/api/admin/plans/',
    listHandler<Plan>({
      items: plans,
      decorate: (plan) => ({
        ...plan,
        active_subscription_count: subscriptions.filter(
          (s) => s.plan.id === plan.id && s.status === 'active',
        ).length,
      }),
      searchFields: ['code', 'name'],
      defaultOrdering: 'sort_order',
      filter: (plan, query) => {
        const isActive = bool(query, 'is_active')
        return isActive === undefined || plan.is_active === isActive
      },
    }),
  ),
  route('GET', `/api/admin/plans/${ID}/`, (spec, params) => {
    requireStaff(spec)
    return findOr404(plans, params[0], 'plan')
  }),
  route('POST', '/api/admin/plans/', (spec) => {
    requireStaff(spec)
    const record = asRecord(spec.body)
    const code = requireString(record, 'code')
    if (plans.some((p) => p.code === code)) {
      fail(400, 'validation_error', { code: ['A plan with this code already exists.'] })
    }
    const created: Plan = {
      id: nextId(plans),
      code,
      name: requireString(record, 'name'),
      price: typeof record.price === 'string' ? record.price : String(record.price ?? '0.00'),
      duration_days: Number(record.duration_days ?? 0),
      max_chain_length: Number(record.max_chain_length ?? 1),
      max_proxy_tier: (record.max_proxy_tier === 'free' ? 'free' : 'premium') as ProxyTier,
      max_devices: Number(record.max_devices ?? 1),
      bandwidth_limit_bytes:
        record.bandwidth_limit_bytes === null || record.bandwidth_limit_bytes === undefined
          ? null
          : Number(record.bandwidth_limit_bytes),
      is_free_tier: record.is_free_tier === true,
      is_active: record.is_active !== false,
      sort_order: Number(record.sort_order ?? 0),
      play_product_id: typeof record.play_product_id === 'string' && record.play_product_id !== ''
        ? record.play_product_id
        : null,
    }
    plans.push(created)
    return created
  }),
  route('PATCH', `/api/admin/plans/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const plan = findOr404(plans, params[0], 'plan')
    const record = asRecord(spec.body)
    if (typeof record.code === 'string') {
      if (plans.some((p) => p.id !== plan.id && p.code === record.code)) {
        fail(400, 'validation_error', { code: ['A plan with this code already exists.'] })
      }
      plan.code = record.code
    }
    if (typeof record.name === 'string') plan.name = record.name
    if (record.price !== undefined) plan.price = String(record.price)
    if (record.duration_days !== undefined) plan.duration_days = Number(record.duration_days)
    if (record.max_chain_length !== undefined) plan.max_chain_length = Number(record.max_chain_length)
    if (record.max_proxy_tier === 'free' || record.max_proxy_tier === 'premium') {
      plan.max_proxy_tier = record.max_proxy_tier
    }
    if (record.max_devices !== undefined) plan.max_devices = Number(record.max_devices)
    if (record.bandwidth_limit_bytes !== undefined) {
      plan.bandwidth_limit_bytes =
        record.bandwidth_limit_bytes === null ? null : Number(record.bandwidth_limit_bytes)
    }
    if (typeof record.is_free_tier === 'boolean') plan.is_free_tier = record.is_free_tier
    if (typeof record.is_active === 'boolean') plan.is_active = record.is_active
    if (record.sort_order !== undefined) plan.sort_order = Number(record.sort_order)
    if (record.play_product_id !== undefined) {
      plan.play_product_id =
        typeof record.play_product_id === 'string' && record.play_product_id !== ''
          ? record.play_product_id
          : null
    }
    return plan
  }),
  route('DELETE', `/api/admin/plans/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const plan = findOr404(plans, params[0], 'plan')
    // Contract guard: refuse with 409 and report the count.
    const activeCount = subscriptions.filter(
      (s) => s.plan.id === plan.id && s.status === 'active',
    ).length
    if (activeCount > 0) {
      fail(
        409,
        'plan_has_active_subscriptions',
        `${activeCount} active subscription${activeCount === 1 ? '' : 's'} still reference this plan. ` +
          'Migrate or cancel them before deleting it.',
        { active_subscriptions: activeCount },
      )
    }
    if (plan.is_free_tier && plans.filter((p) => p.is_free_tier).length === 1) {
      fail(
        409,
        'last_free_plan',
        'This is the only free-tier plan. New accounts would have no entitlement without it.',
      )
    }
    plans.splice(plans.indexOf(plan), 1)
    return null
  }),

  /* ---- Subscriptions ----------------------------------------------- */
  route(
    'GET',
    '/api/admin/subscriptions/',
    listHandler<Subscription>({
      items: subscriptions,
      searchFields: ['user__email'],
      defaultOrdering: '-started_at',
      filter: (sub, query) => {
        const status = str(query, 'status')
        const plan = num(query, 'plan')
        const user = num(query, 'user')
        if (status && sub.status !== status) return false
        if (plan !== undefined && sub.plan.id !== plan) return false
        if (user !== undefined && sub.user.id !== user) return false
        return true
      },
    }),
  ),
  route('GET', `/api/admin/subscriptions/${ID}/`, (spec, params) => {
    requireStaff(spec)
    return findOr404(subscriptions, params[0], 'subscription')
  }),
  route('POST', '/api/admin/subscriptions/', (spec) => {
    requireStaff(spec)
    const record = asRecord(spec.body)
    // Writes accept `user_id`/`plan_id` or a bare integer `user`/`plan`.
    const userRef = record.user_id ?? record.user
    const planRef = record.plan_id ?? record.plan
    const user = users.find((u) => u.id === Number(userRef))
    if (!user) fail(400, 'validation_error', { user_id: ['Select a user.'] })
    const plan = plans.find((p) => p.id === Number(planRef))
    if (!plan) fail(400, 'validation_error', { plan_id: ['Select a plan.'] })
    const created: Subscription = {
      id: nextId(subscriptions),
      user: { id: user.id, email: user.email },
      plan: { id: plan.id, code: plan.code, name: plan.name },
      started_at: typeof record.started_at === 'string' ? record.started_at : new Date().toISOString(),
      expires_at: typeof record.expires_at === 'string' && record.expires_at !== '' ? record.expires_at : null,
      status: (typeof record.status === 'string' ? record.status : 'active') as SubscriptionStatus,
      platform: (record.platform === 'play' || record.platform === 'stripe' ? record.platform : 'manual'),
      platform_txn_id:
        typeof record.platform_txn_id === 'string' && record.platform_txn_id !== ''
          ? record.platform_txn_id
          : null,
      last_verified_at: null,
    }
    subscriptions.unshift(created)
    return created
  }),
  route('PATCH', `/api/admin/subscriptions/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const sub = findOr404(subscriptions, params[0], 'subscription')
    const record = asRecord(spec.body)
    const planRef = record.plan_id ?? record.plan
    if (planRef !== undefined) {
      const plan = plans.find((p) => p.id === Number(planRef))
      if (!plan) fail(400, 'validation_error', { plan_id: ['Select a plan.'] })
      sub.plan = { id: plan.id, code: plan.code, name: plan.name }
    }
    if (typeof record.started_at === 'string') sub.started_at = record.started_at
    if (record.expires_at !== undefined) {
      sub.expires_at =
        typeof record.expires_at === 'string' && record.expires_at !== '' ? record.expires_at : null
    }
    if (typeof record.status === 'string') sub.status = record.status as SubscriptionStatus
    if (record.platform === 'play' || record.platform === 'stripe' || record.platform === 'manual') {
      sub.platform = record.platform
    }
    if (record.platform_txn_id !== undefined) {
      sub.platform_txn_id =
        typeof record.platform_txn_id === 'string' && record.platform_txn_id !== ''
          ? record.platform_txn_id
          : null
    }
    return sub
  }),
  route('DELETE', `/api/admin/subscriptions/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const sub = findOr404(subscriptions, params[0], 'subscription')
    subscriptions.splice(subscriptions.indexOf(sub), 1)
    return null
  }),
  route('POST', `/api/admin/subscriptions/${ID}/cancel/`, (spec, params) => {
    requireStaff(spec)
    const sub = findOr404(subscriptions, params[0], 'subscription')
    if (sub.status === 'cancelled') {
      fail(409, 'already_cancelled', 'This subscription is already cancelled.')
    }
    sub.status = 'cancelled'
    sub.last_verified_at = new Date().toISOString()
    return sub
  }),

  /* ---- Proxies ----------------------------------------------------- */
  route('POST', '/api/admin/proxies/bulk_enable/', async (spec) => {
    requireStaff(spec)
    const ids = numberIds(spec.body)
    const record = asRecord(spec.body)
    const isEnabled = record.is_enabled === true
    let updated = 0
    for (const proxy of proxies) {
      if (ids.includes(proxy.id) && proxy.is_enabled !== isEnabled) {
        proxy.is_enabled = isEnabled
        updated += 1
      }
    }
    const response: BulkEnableResponse = { updated }
    return response
  }),
  route('POST', '/api/admin/proxies/bulk_delete/', (spec) => {
    requireStaff(spec)
    const ids = new Set(numberIds(spec.body))

    // Refused as a whole, not partially: a proxy still used by a saved chain
    // blocks the batch and nothing is deleted.
    const blocking = [
      ...new Set(
        chains.flatMap((chain) =>
          chain.hops.map((hop) => hop.proxy.id).filter((proxyId) => ids.has(proxyId)),
        ),
      ),
    ].sort((a, b) => a - b)

    if (blocking.length > 0) {
      fail(
        409,
        'proxy_in_use',
        blocking.length +
          ' of the selected ' +
          (blocking.length === 1 ? 'proxies is' : 'proxies are') +
          ' still used by a saved user chain. Nothing was deleted.',
        { proxy_ids: blocking },
      )
    }

    let deleted = 0
    for (let i = proxies.length - 1; i >= 0; i -= 1) {
      if (ids.has(proxies[i]!.id)) {
        proxies.splice(i, 1)
        deleted += 1
      }
    }
    const response: BulkDeleteResponse = { deleted }
    return response
  }),
  route('POST', '/api/admin/proxies/upload_csv/', async (spec) => {
    requireStaff(spec)
    const file = spec.formData?.get('file')
    if (!(file instanceof File)) {
      fail(400, 'validation_error', { file: ['No file was submitted.'] })
    }
    if (!file.name.toLowerCase().endsWith('.csv')) {
      fail(400, 'validation_error', { file: ['Expected a .csv file.'] })
    }
    // Simulate the upload so the progress bar is driven by real events.
    if (spec.onProgress) {
      for (let step = 1; step <= 10; step += 1) {
        await delay(55)
        spec.onProgress(step / 10)
      }
    }
    return importCsv(file)
  }),
  route(
    'GET',
    '/api/admin/proxies/',
    (spec) => {
      requireStaff(spec)
      const ids = idSet(spec.query)
      let items = proxies.map(toListItem)
      if (ids) items = items.filter((item) => ids.has(item.id))
      const protocol = str(spec.query, 'protocol')
      const tier = str(spec.query, 'tier')
      const country = str(spec.query, 'country')
      const isAlive = bool(spec.query, 'is_alive')
      const isEnabled = bool(spec.query, 'is_enabled')
      items = items.filter((proxy) => {
        if (protocol && proxy.protocol !== protocol) return false
        if (tier && proxy.tier !== tier) return false
        if (country && proxy.country !== country) return false
        if (isAlive !== undefined && proxy.is_alive !== isAlive) return false
        if (isEnabled !== undefined && proxy.is_enabled !== isEnabled) return false
        return true
      })
      items = applySearch(items, str(spec.query, 'search'), ['host', 'country', 'city'])
      items = applyOrdering(items, str(spec.query, 'ordering'), 'host')
      return paginate(items, spec.query)
    },
  ),
  route('GET', `/api/admin/proxies/${ID}/`, (spec, params) => {
    requireStaff(spec)
    // Detail only: credentials are decrypted here and nowhere else.
    return findOr404(proxies, params[0], 'proxy')
  }),
  route('POST', '/api/admin/proxies/', (spec) => {
    requireStaff(spec)
    const record = asRecord(spec.body)
    const host = requireString(record, 'host')
    const port = Number(record.port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      fail(400, 'validation_error', { port: ['Enter an integer between 1 and 65535.'] })
    }
    if (proxies.some((p) => p.host === host && p.port === port)) {
      fail(400, 'validation_error', { host: [`${host}:${port} already exists.`] })
    }
    const protocol = (record.protocol ?? 'socks5') as Protocol
    if (protocol === 'ss' && typeof record.ss_method !== 'string') {
      fail(400, 'validation_error', { ss_method: ['Required when the protocol is Shadowsocks.'] })
    }
    const created: ProxyDetail = {
      id: nextId(proxies),
      protocol,
      host,
      port,
      country: requireString(record, 'country').toUpperCase(),
      city: typeof record.city === 'string' && record.city !== '' ? record.city : null,
      tier: (record.tier === 'free' ? 'free' : 'premium') as ProxyTier,
      is_alive: false,
      is_enabled: record.is_enabled !== false,
      latency_ms: null,
      last_checked: null,
      requires_credentials: typeof record.password === 'string' && record.password !== '',
      upload_batch: null,
      username: typeof record.username === 'string' && record.username !== '' ? record.username : null,
      password: typeof record.password === 'string' && record.password !== '' ? record.password : null,
      ss_method: typeof record.ss_method === 'string' && record.ss_method !== '' ? record.ss_method : null,
    }
    proxies.unshift(created)
    return created
  }),
  route('PATCH', `/api/admin/proxies/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const proxy = findOr404(proxies, params[0], 'proxy')
    const record = asRecord(spec.body)
    if (typeof record.host === 'string') proxy.host = record.host
    if (record.port !== undefined) {
      const port = Number(record.port)
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        fail(400, 'validation_error', { port: ['Enter an integer between 1 and 65535.'] })
      }
      proxy.port = port
    }
    if (typeof record.protocol === 'string') proxy.protocol = record.protocol as Protocol
    if (typeof record.country === 'string') proxy.country = record.country.toUpperCase()
    if (record.city !== undefined) {
      proxy.city = typeof record.city === 'string' && record.city !== '' ? record.city : null
    }
    if (record.tier === 'free' || record.tier === 'premium') proxy.tier = record.tier
    if (typeof record.is_enabled === 'boolean') proxy.is_enabled = record.is_enabled
    if (record.username !== undefined) {
      proxy.username = typeof record.username === 'string' && record.username !== '' ? record.username : null
    }
    if (record.password !== undefined) {
      proxy.password = typeof record.password === 'string' && record.password !== '' ? record.password : null
      proxy.requires_credentials = proxy.password !== null
    }
    if (record.ss_method !== undefined) {
      proxy.ss_method = typeof record.ss_method === 'string' && record.ss_method !== '' ? record.ss_method : null
    }
    return proxy
  }),
  route('DELETE', `/api/admin/proxies/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const proxy = findOr404(proxies, params[0], 'proxy')
    proxies.splice(proxies.indexOf(proxy), 1)
    return null
  }),

  /* ---- Upload batches (read-only) ---------------------------------- */
  route(
    'GET',
    '/api/admin/upload-batches/',
    listHandler<UploadBatch>({
      items: uploadBatches,
      searchFields: ['filename', 'uploaded_by'],
      defaultOrdering: '-created_at',
    }),
  ),
  route('GET', `/api/admin/upload-batches/${ID}/`, (spec, params) => {
    requireStaff(spec)
    return findOr404(uploadBatches, params[0], 'upload batch')
  }),

  /* ---- Content ------------------------------------------------------ */
  route(
    'GET',
    '/api/admin/content/',
    listHandler<ContentItem>({
      items: content,
      searchFields: ['key', 'title', 'body'],
      defaultOrdering: '-priority',
      filter: (item, query) => {
        const kind = str(query, 'kind')
        const published = bool(query, 'is_published')
        if (kind && item.kind !== kind) return false
        if (published !== undefined && item.is_published !== published) return false
        return true
      },
    }),
  ),
  route('GET', `/api/admin/content/${ID}/`, (spec, params) => {
    requireStaff(spec)
    return findOr404(content, params[0], 'content item')
  }),
  route('POST', '/api/admin/content/', (spec) => {
    requireStaff(spec)
    const record = asRecord(spec.body)
    const key = requireString(record, 'key')
    if (content.some((c) => c.key === key)) {
      fail(400, 'validation_error', { key: ['A content item with this key already exists.'] })
    }
    const now = new Date().toISOString()
    const created: ContentItem = {
      id: nextId(content),
      key,
      kind: typeof record.kind === 'string' ? record.kind : 'announcement',
      title: requireString(record, 'title'),
      body: typeof record.body === 'string' ? record.body : '',
      priority: Number(record.priority ?? 0),
      is_published: record.is_published === true,
      created_at: now,
      updated_at: now,
    }
    content.unshift(created)
    return created
  }),
  route('PATCH', `/api/admin/content/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const item = findOr404(content, params[0], 'content item')
    const record = asRecord(spec.body)
    if (typeof record.key === 'string') {
      if (content.some((c) => c.id !== item.id && c.key === record.key)) {
        fail(400, 'validation_error', { key: ['A content item with this key already exists.'] })
      }
      item.key = record.key
    }
    if (typeof record.kind === 'string') item.kind = record.kind
    if (typeof record.title === 'string') item.title = record.title
    if (typeof record.body === 'string') item.body = record.body
    if (record.priority !== undefined) item.priority = Number(record.priority)
    if (typeof record.is_published === 'boolean') item.is_published = record.is_published
    item.updated_at = new Date().toISOString()
    return item
  }),
  route('DELETE', `/api/admin/content/${ID}/`, (spec, params) => {
    requireStaff(spec)
    const item = findOr404(content, params[0], 'content item')
    content.splice(content.indexOf(item), 1)
    return null
  }),

  /* ---- Usage logs (read-only) --------------------------------------- */
  route(
    'GET',
    '/api/admin/usage-logs/',
    listHandler<UsageLog>({
      items: usageLogs,
      searchFields: ['user.email'],
      defaultOrdering: '-session_start',
      filter: (log, query) => {
        const user = num(query, 'user')
        const after = str(query, 'session_start_after')
        const before = str(query, 'session_start_before')
        if (user !== undefined && log.user.id !== user) return false
        if (after && log.session_start < after) return false
        if (before && log.session_start > before) return false
        return true
      },
    }),
  ),

  /* ---- Chains (read-only) ------------------------------------------- */
  route(
    'GET',
    '/api/admin/chains/',
    listHandler<Chain>({
      items: chains,
      searchFields: ['name', 'user.email'],
      defaultOrdering: '-created_at',
      filter: (chain, query) => {
        const user = num(query, 'user')
        return user === undefined || chain.user.id === user
      },
    }),
  ),
]

/** The in-memory transport. Same interface as the HTTP one. */
export function createMockTransport(latencyMs: number = env.mockLatencyMs): Transport {
  return {
    async request<T>(spec: RequestSpec): Promise<T> {
      // Jittered, so loading states look like a network rather than a metronome.
      await delay(latencyMs * (0.6 + Math.random() * 0.8))
      if (spec.signal?.aborted) throw new DOMException('Aborted', 'AbortError')

      for (const candidate of routes) {
        if (candidate.method !== spec.method) continue
        const match = candidate.pattern.exec(spec.path)
        if (!match) continue
        const result = await candidate.handle(spec, match.slice(1))
        return (result ?? null) as T
      }

      return fail(404, 'not_found', `No mock route for ${spec.method} ${spec.path}`)
    },
  }
}
