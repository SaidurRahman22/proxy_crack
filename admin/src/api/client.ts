/**
 * The typed admin API client.
 *
 * Every call site in the app goes through this module. It is the only place
 * that knows whether the app is talking to Django or to the in-memory mock —
 * `env.useMockApi` decides, and nothing above here changes.
 */

import { env } from '@/env'

import { createHttpTransport } from './httpTransport'
import { tokenStore } from './tokenStore'
import { withAuthRefresh, type QueryParams, type RequestSpec, type Transport } from './transport'
import type {
  AdminUser,
  ProxyCredentials,
  BulkDeleteResponse,
  BulkEnableResponse,
  Chain,
  ContentItem,
  ContentWrite,
  CsvUploadResponse,
  LoginRequest,
  LoginResponse,
  Paginated,
  Plan,
  PlanWrite,
  Protocol,
  ProxyDetail,
  ProxyListItem,
  ProxyTier,
  ProxyWrite,
  Stats,
  Subscription,
  SubscriptionPlatform,
  SubscriptionStatus,
  SubscriptionWrite,
  UploadBatch,
  UsageLog,
  User,
  UserWrite,
} from './types'

/* ------------------------------------------------------------------ *
 * List parameters
 *
 * Type aliases, not interfaces: an alias of an object literal type is
 * assignable to `QueryParams` without an explicit index signature.
 * ------------------------------------------------------------------ */

export type ListParams = {
  page?: number
  page_size?: number
  search?: string
  ordering?: string
  /** `?ids=1,2,3` — bulk-selection round-trips. */
  ids?: number[]
}

export type UserListParams = ListParams & {
  is_active?: boolean
  is_staff?: boolean
}

export type PlanListParams = ListParams & {
  is_active?: boolean
}

export type SubscriptionListParams = ListParams & {
  status?: SubscriptionStatus
  plan?: number
  user?: number
}

export type ProxyListParams = ListParams & {
  protocol?: Protocol
  tier?: ProxyTier
  country?: string
  is_alive?: boolean
  is_enabled?: boolean
}

export type ContentListParams = ListParams & {
  kind?: string
  is_published?: boolean
}

export type UsageLogListParams = ListParams & {
  user?: number
  /**
   * django-filter `DateFromToRangeFilter` suffixes. If the backend names the
   * range differently, this is the only place to change.
   */
  session_start_after?: string
  session_start_before?: string
}

export type ChainListParams = ListParams & {
  user?: number
}

/* ------------------------------------------------------------------ *
 * Session-lost hook
 * ------------------------------------------------------------------ */

let sessionLostHandler: () => void = () => {}

/**
 * Registered by the auth provider. Fires when a refresh fails or the single
 * permitted retry still comes back 401 — i.e. the session is unrecoverable
 * and the user must be sent back to the login screen.
 */
export function onSessionLost(handler: () => void): void {
  sessionLostHandler = handler
}

/* ------------------------------------------------------------------ *
 * Transport assembly
 * ------------------------------------------------------------------ */

/**
 * The mock is loaded through a dynamic import so Rollup emits it as its own
 * chunk. With `VITE_USE_MOCK_API=false` that chunk is never fetched, so the
 * 640-row fixture generator costs a production build nothing — neither bytes
 * on the critical path nor startup time.
 */
function createLazyMockTransport(): Transport {
  let pending: Promise<Transport> | null = null
  return {
    async request<T>(spec: RequestSpec): Promise<T> {
      pending ??= import('./mock/mockTransport').then((module) => module.createMockTransport())
      const real = await pending
      return real.request<T>(spec)
    },
  }
}

const baseTransport: Transport = env.useMockApi ? createLazyMockTransport() : createHttpTransport()

export const transport: Transport = withAuthRefresh(baseTransport, {
  getRefreshToken: () => tokenStore.getRefresh(),
  setTokens: (tokens) => {
    tokenStore.setAccess(tokens.access)
    // Rotation is on server-side: the old refresh token is already blacklisted.
    tokenStore.setRefresh(tokens.refresh)
  },
  onSessionLost: () => {
    tokenStore.clear()
    sessionLostHandler()
  },
})

const BASE = '/api/admin'

const get = <T,>(path: string, query?: QueryParams, signal?: AbortSignal): Promise<T> =>
  transport.request<T>({ method: 'GET', path, query, signal })

const post = <T,>(path: string, body?: unknown): Promise<T> =>
  transport.request<T>({ method: 'POST', path, body })

const patch = <T,>(path: string, body: unknown): Promise<T> =>
  transport.request<T>({ method: 'PATCH', path, body })

const destroy = (path: string): Promise<null> =>
  transport.request<null>({ method: 'DELETE', path })

/* ------------------------------------------------------------------ *
 * The client
 * ------------------------------------------------------------------ */

export const api = {
  auth: {
    login: (body: LoginRequest): Promise<LoginResponse> =>
      transport.request<LoginResponse>({
        method: 'POST',
        path: `${BASE}/auth/login/`,
        body,
        auth: false,
      }),

    logout: (refresh: string): Promise<null> =>
      transport.request<null>({
        method: 'POST',
        path: `${BASE}/auth/logout/`,
        body: { refresh },
        auth: false,
      }),

    me: (signal?: AbortSignal): Promise<AdminUser> => get<AdminUser>(`${BASE}/auth/me/`, undefined, signal),
  },

  stats: {
    get: (signal?: AbortSignal): Promise<Stats> => get<Stats>(`${BASE}/stats/`, undefined, signal),
  },

  users: {
    list: (params: UserListParams, signal?: AbortSignal): Promise<Paginated<User>> =>
      get<Paginated<User>>(`${BASE}/users/`, params, signal),
    get: (id: number, signal?: AbortSignal): Promise<User> =>
      get<User>(`${BASE}/users/${id}/`, undefined, signal),
    create: (body: UserWrite): Promise<User> => post<User>(`${BASE}/users/`, body),
    update: (id: number, body: Partial<UserWrite>): Promise<User> =>
      patch<User>(`${BASE}/users/${id}/`, body),
    remove: (id: number): Promise<null> => destroy(`${BASE}/users/${id}/`),
    setPassword: (id: number, password: string): Promise<null> =>
      post<null>(`${BASE}/users/${id}/set_password/`, { password }),
  },

  plans: {
    list: (params: PlanListParams, signal?: AbortSignal): Promise<Paginated<Plan>> =>
      get<Paginated<Plan>>(`${BASE}/plans/`, params, signal),
    get: (id: number, signal?: AbortSignal): Promise<Plan> =>
      get<Plan>(`${BASE}/plans/${id}/`, undefined, signal),
    create: (body: PlanWrite): Promise<Plan> => post<Plan>(`${BASE}/plans/`, body),
    update: (id: number, body: Partial<PlanWrite>): Promise<Plan> =>
      patch<Plan>(`${BASE}/plans/${id}/`, body),
    /** 409 with the active-subscription count when the plan is still in use. */
    remove: (id: number): Promise<null> => destroy(`${BASE}/plans/${id}/`),
  },

  subscriptions: {
    list: (params: SubscriptionListParams, signal?: AbortSignal): Promise<Paginated<Subscription>> =>
      get<Paginated<Subscription>>(`${BASE}/subscriptions/`, params, signal),
    get: (id: number, signal?: AbortSignal): Promise<Subscription> =>
      get<Subscription>(`${BASE}/subscriptions/${id}/`, undefined, signal),
    create: (body: SubscriptionWrite): Promise<Subscription> =>
      post<Subscription>(`${BASE}/subscriptions/`, body),
    update: (id: number, body: Partial<SubscriptionWrite>): Promise<Subscription> =>
      patch<Subscription>(`${BASE}/subscriptions/${id}/`, body),
    remove: (id: number): Promise<null> => destroy(`${BASE}/subscriptions/${id}/`),
    cancel: (id: number): Promise<Subscription> =>
      post<Subscription>(`${BASE}/subscriptions/${id}/cancel/`),
  },

  proxies: {
    list: (params: ProxyListParams, signal?: AbortSignal): Promise<Paginated<ProxyListItem>> =>
      get<Paginated<ProxyListItem>>(`${BASE}/proxies/`, params, signal),
    get: (id: number, signal?: AbortSignal): Promise<ProxyDetail> =>
      get<ProxyDetail>(`${BASE}/proxies/${id}/`, undefined, signal),

    /**
     * Discloses the decrypted credentials for one proxy.
     *
     * A POST, not a GET, so the server audit log records a deliberate
     * disclosure rather than a page view, and so the action is not
     * replayable from history or a prefetch. Throttled server-side
     * (120/hour, 400/day).
     *
     * The caller must not persist, cache or log the result —
     * see `useProxyCredentials`, which holds it in component state only.
     */
    reveal: (id: number, signal?: AbortSignal): Promise<ProxyCredentials> =>
      transport.request<ProxyCredentials>({
        method: 'POST',
        path: `${BASE}/proxies/${id}/reveal/`,
        signal,
      }),
    create: (body: ProxyWrite): Promise<ProxyDetail> => post<ProxyDetail>(`${BASE}/proxies/`, body),
    update: (id: number, body: Partial<ProxyWrite>): Promise<ProxyDetail> =>
      patch<ProxyDetail>(`${BASE}/proxies/${id}/`, body),
    remove: (id: number): Promise<null> => destroy(`${BASE}/proxies/${id}/`),

    bulkEnable: (ids: number[], isEnabled: boolean): Promise<BulkEnableResponse> =>
      post<BulkEnableResponse>(`${BASE}/proxies/bulk_enable/`, { ids, is_enabled: isEnabled }),
    bulkDelete: (ids: number[]): Promise<BulkDeleteResponse> =>
      post<BulkDeleteResponse>(`${BASE}/proxies/bulk_delete/`, { ids }),

    uploadCsv: (
      file: File,
      onProgress?: (fraction: number) => void,
      signal?: AbortSignal,
    ): Promise<CsvUploadResponse> => {
      const formData = new FormData()
      formData.append('file', file)
      return transport.request<CsvUploadResponse>({
        method: 'POST',
        path: `${BASE}/proxies/upload_csv/`,
        formData,
        onProgress,
        signal,
      })
    },
  },

  uploadBatches: {
    list: (params: ListParams, signal?: AbortSignal): Promise<Paginated<UploadBatch>> =>
      get<Paginated<UploadBatch>>(`${BASE}/upload-batches/`, params, signal),
    get: (id: number, signal?: AbortSignal): Promise<UploadBatch> =>
      get<UploadBatch>(`${BASE}/upload-batches/${id}/`, undefined, signal),
  },

  content: {
    list: (params: ContentListParams, signal?: AbortSignal): Promise<Paginated<ContentItem>> =>
      get<Paginated<ContentItem>>(`${BASE}/content/`, params, signal),
    get: (id: number, signal?: AbortSignal): Promise<ContentItem> =>
      get<ContentItem>(`${BASE}/content/${id}/`, undefined, signal),
    create: (body: ContentWrite): Promise<ContentItem> => post<ContentItem>(`${BASE}/content/`, body),
    update: (id: number, body: Partial<ContentWrite>): Promise<ContentItem> =>
      patch<ContentItem>(`${BASE}/content/${id}/`, body),
    remove: (id: number): Promise<null> => destroy(`${BASE}/content/${id}/`),
  },

  usageLogs: {
    list: (params: UsageLogListParams, signal?: AbortSignal): Promise<Paginated<UsageLog>> =>
      get<Paginated<UsageLog>>(`${BASE}/usage-logs/`, params, signal),
  },

  chains: {
    list: (params: ChainListParams, signal?: AbortSignal): Promise<Paginated<Chain>> =>
      get<Paginated<Chain>>(`${BASE}/chains/`, params, signal),
  },
} as const

export type Api = typeof api

/** Re-exported so `SubscriptionPlatform` consumers need one import. */
export type { SubscriptionPlatform }
