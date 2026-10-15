/**
 * Types for the Proxy Crack admin API.
 *
 * Transcribed from `Project/admin-api-contract.md`. This file is the single
 * place the contract is encoded; nothing in the API layer may use `any`.
 *
 * Where the contract names a field but not its value domain, the union here is
 * a documented assumption marked `@contract-assumption`. If the Django side
 * lands on different literals, change the union — no other file needs editing.
 */

/* ------------------------------------------------------------------ *
 * Envelope + pagination
 * ------------------------------------------------------------------ */

/** `{count, next, previous, results[]}` — every list endpoint. */
export interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

/**
 * The error body: `{"error": {"code", "status", "detail"}}`.
 *
 * `detail` is a string for a whole-request failure, or a DRF-style
 * field -> messages map when a serializer rejects the payload. Both shapes are
 * surfaced inline by the form layer.
 */
export type ErrorDetail = string | string[] | Record<string, string | string[]>

/**
 * The error object may carry extra, code-specific members alongside the three
 * the contract names — `active_subscriptions` on a 409 plan guard,
 * `proxy_ids` on a refused bulk delete. They are preserved on `ApiError.extra`
 * so a screen can say which records blocked the operation.
 */
export interface ApiErrorEnvelope {
  error: {
    code: string
    status: number
    detail: ErrorDetail
    [key: string]: unknown
  }
}

/** Error codes the backend emits that the UI reacts to specifically. */
export const ERROR_CODES = {
  /** 403 — token was not minted by the admin login. Never retryable. */
  adminScopeRequired: 'admin_scope_required',
  privilegeChangeForbidden: 'privilege_change_forbidden',
  setPasswordForbidden: 'set_password_forbidden',
  /** 409 — carries `active_subscriptions: number`. */
  planHasActiveSubscriptions: 'plan_has_active_subscriptions',
  planInUse: 'plan_in_use',
  /** 409 — carries `proxy_ids: number[]`. Nothing was deleted. */
  proxyInUse: 'proxy_in_use',
  cannotDeleteSelf: 'cannot_delete_self',
  lastFreePlan: 'last_free_plan',
} as const

/* ------------------------------------------------------------------ *
 * Auth
 * ------------------------------------------------------------------ */

export interface AdminUser {
  id: number
  email: string
  first_name: string
  last_name: string
  is_staff: boolean
  is_superuser: boolean
}

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  access: string
  refresh: string
  user: AdminUser
}

/**
 * The backend runs SimpleJWT with `ROTATE_REFRESH_TOKENS` and
 * `BLACKLIST_AFTER_ROTATION` both on, so a refresh call returns a NEW refresh
 * token and blacklists the one that was submitted. Both values must be
 * stored — keeping the old refresh token logs the operator out at the next
 * refresh.
 */
export interface RefreshResponse {
  access: string
  refresh: string
}

/* ------------------------------------------------------------------ *
 * Dashboard
 * ------------------------------------------------------------------ */

export const PROTOCOLS = ['ss', 'socks5', 'http'] as const
export type Protocol = (typeof PROTOCOLS)[number]

/** @contract-assumption tiers are the two the stats payload counts. */
export const PROXY_TIERS = ['free', 'premium'] as const
export type ProxyTier = (typeof PROXY_TIERS)[number]

export interface Stats {
  users: { total: number; staff: number; new_7d: number }
  subscriptions: { active: number; expired: number; free_tier: number; paid: number }
  proxies: {
    total: number
    alive: number
    dead: number
    free: number
    premium: number
    by_protocol: Record<Protocol, number>
  }
  usage: { bytes_up_7d: number; bytes_down_7d: number; sessions_7d: number }
  uploads: { last_batch_at: string | null; last_batch_rows: number }
}

/* ------------------------------------------------------------------ *
 * Users
 * ------------------------------------------------------------------ */

/**
 * `subscription (nested summary)` on the user record. The contract names the
 * field but not its members; these are the ones the list view renders.
 * @contract-assumption
 */
export interface SubscriptionSummary {
  id: number
  plan_code: string
  plan_name: string
  status: SubscriptionStatus
  expires_at: string | null
}

export interface User {
  id: number
  email: string
  first_name: string
  last_name: string
  is_active: boolean
  is_staff: boolean
  is_superuser: boolean
  date_joined: string
  last_login: string | null
  subscription: SubscriptionSummary | null
}

export interface UserWrite {
  email: string
  first_name: string
  last_name: string
  is_active: boolean
  is_staff: boolean
  is_superuser: boolean
  /** Only sent on create. Changing a password later goes via set_password. */
  password?: string
}

export interface SetPasswordRequest {
  password: string
}

/* ------------------------------------------------------------------ *
 * Plans
 * ------------------------------------------------------------------ */

export interface Plan {
  id: number
  code: string
  name: string
  /** DRF serialises DecimalField to a string by default. */
  price: string
  /** Beyond the contract; present on the built backend. */
  currency?: string
  description?: string
  /** Read-only count the 409 delete guard is derived from. */
  active_subscription_count?: number
  duration_days: number
  max_chain_length: number
  max_proxy_tier: ProxyTier
  max_devices: number
  bandwidth_limit_bytes: number | null
  is_free_tier: boolean
  is_active: boolean
  sort_order: number
  play_product_id: string | null
}

export type PlanWrite = Omit<Plan, 'id' | 'active_subscription_count'>

/* ------------------------------------------------------------------ *
 * Subscriptions
 * ------------------------------------------------------------------ */

/** @contract-assumption `cancelled`/`pending` inferred from the cancel action. */
export const SUBSCRIPTION_STATUSES = ['active', 'expired', 'cancelled', 'pending'] as const
export type SubscriptionStatus = (typeof SUBSCRIPTION_STATUSES)[number]

/** @contract-assumption Play is mandatory on Android; the desktop rail is still open (P6). */
export const SUBSCRIPTION_PLATFORMS = ['play', 'stripe', 'manual'] as const
export type SubscriptionPlatform = (typeof SUBSCRIPTION_PLATFORMS)[number]

export interface UserRef {
  id: number
  email: string
}

export interface PlanRef {
  id: number
  code: string
  name: string
}

export interface Subscription {
  id: number
  user: UserRef
  plan: PlanRef
  started_at: string
  expires_at: string | null
  status: SubscriptionStatus
  platform: SubscriptionPlatform
  platform_txn_id: string | null
  last_verified_at: string | null
}

/**
 * Nested reads, flat writes. The backend accepts `user_id`/`plan_id` or a
 * bare integer `user`/`plan`; the explicit `_id` form is used here so there is
 * no ambiguity with the nested read fields of the same name.
 */
export interface SubscriptionWrite {
  user_id: number
  plan_id: number
  started_at: string
  expires_at: string | null
  status: SubscriptionStatus
  platform: SubscriptionPlatform
  platform_txn_id: string | null
}

/* ------------------------------------------------------------------ *
 * Proxies
 * ------------------------------------------------------------------ */

export interface ProxyListItem {
  id: number
  protocol: Protocol
  host: string
  port: number
  country: string
  city: string | null
  tier: ProxyTier
  is_alive: boolean
  is_enabled: boolean
  latency_ms: number | null
  last_checked: string | null
  requires_credentials: boolean
  upload_batch: number | null
}

/**
 * The detail endpoint adds decrypted credentials.
 *
 * These values must never reach localStorage, sessionStorage, IndexedDB, the
 * URL, the TanStack Query cache, or a log line. See `useProxyCredentials` —
 * it is deliberately NOT a useQuery hook for exactly that reason.
 */
export interface ProxyCredentials {
  username: string | null
  password: string | null
  ss_method: string | null
}

export type ProxyDetail = ProxyListItem & ProxyCredentials

export interface ProxyWrite {
  protocol: Protocol
  host: string
  port: number
  country: string
  city: string | null
  tier: ProxyTier
  is_enabled: boolean
  username: string | null
  password: string | null
  ss_method: string | null
}

export interface BulkEnableRequest {
  ids: number[]
  is_enabled: boolean
}

export interface BulkEnableResponse {
  updated: number
}

export interface BulkDeleteRequest {
  ids: number[]
}

export interface BulkDeleteResponse {
  deleted: number
}

/* ------------------------------------------------------------------ *
 * CSV upload + batches
 * ------------------------------------------------------------------ */

export interface RowError {
  line: number
  reason: string
}

export interface CsvUploadResponse {
  batch_id: number
  row_count: number
  created_count: number
  updated_count: number
  fail_count: number
  errors: RowError[]
}

export interface UploadBatch {
  id: number
  filename: string
  uploaded_by: string
  row_count: number
  success_count: number
  created_count: number
  updated_count: number
  fail_count: number
  /** The parsed report the error view renders. */
  errors: RowError[]
  /** Raw text of the same report; `errors` is preferred. Beyond the contract. */
  errors_text?: string
  created_at: string
}

/* ------------------------------------------------------------------ *
 * Content
 * ------------------------------------------------------------------ */

/** @contract-assumption `kind` is open — the form suggests these but accepts any string. */
export const CONTENT_KINDS = ['announcement', 'faq', 'legal', 'tip'] as const

export interface ContentItem {
  id: number
  key: string
  kind: string
  title: string
  body: string
  priority: number
  is_published: boolean
  created_at: string
  updated_at: string
}

export type ContentWrite = Omit<ContentItem, 'id' | 'created_at' | 'updated_at'>

/* ------------------------------------------------------------------ *
 * Usage logs + chains (read-only)
 * ------------------------------------------------------------------ */

export interface UsageLog {
  id: number
  user: UserRef
  bytes_up: number
  bytes_down: number
  /** Beyond the contract; the UI falls back to up + down when absent. */
  bytes_total?: number
  session_start: string
  session_end: string | null
}

export interface ChainHop {
  position: number
  proxy: {
    id: number
    protocol: Protocol
    host: string
    country: string
  }
}

export interface Chain {
  id: number
  user: UserRef
  name: string
  hops: ChainHop[]
  /** Hop count. Beyond the contract; falls back to `hops.length`. */
  length?: number
  created_at: string
}
