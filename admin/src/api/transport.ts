import { ApiError } from './errors'
import type { RefreshResponse } from './types'

export type QueryValue = string | number | boolean | null | undefined | Array<string | number>
export type QueryParams = Record<string, QueryValue>

export interface RequestSpec {
  method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  path: string
  query?: QueryParams
  /** JSON request body. Mutually exclusive with `formData`. */
  body?: unknown
  /** Multipart body (CSV upload). Enables the progress-capable code path. */
  formData?: FormData
  /** Attach the bearer token. Default true; the auth endpoints opt out. */
  auth?: boolean
  signal?: AbortSignal
  /** Upload progress, 0..1. Only honoured alongside `formData`. */
  onProgress?: (fraction: number) => void
}

/**
 * The seam between the app and the network.
 *
 * `httpTransport` talks to Django; `mockTransport` answers from memory against
 * the same contract. Swapping them is a one-line change in `client.ts`, driven
 * by an env var — no call site is aware of the difference.
 */
export interface Transport {
  request<T>(spec: RequestSpec): Promise<T>
}

export function buildQueryString(query: QueryParams | undefined): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      if (value.length === 0) continue
      params.set(key, value.join(','))
    } else {
      params.set(key, String(value))
    }
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

interface AuthRefreshOptions {
  getRefreshToken: () => string | null
  /**
   * Stores BOTH tokens. The backend rotates refresh tokens and blacklists the
   * submitted one, so persisting only `access` logs the operator out at the
   * very next refresh.
   */
  setTokens: (tokens: RefreshResponse) => void
  /** Called once the session is unrecoverable. Clears state and redirects. */
  onSessionLost: () => void
}

const REFRESH_PATH = '/api/admin/auth/refresh/'

/**
 * Wraps a transport with refresh-on-401.
 *
 * Exactly one retry. Concurrent 401s share a single in-flight refresh instead
 * of stampeding the endpoint, and a failed refresh tears the session down
 * rather than looping.
 */
export function withAuthRefresh(base: Transport, options: AuthRefreshOptions): Transport {
  let inFlight: Promise<string> | null = null

  async function refreshOnce(): Promise<string> {
    if (inFlight) return inFlight
    inFlight = (async () => {
      const refresh = options.getRefreshToken()
      if (!refresh) throw new ApiError(401, 'no_refresh_token', 'Session expired.')
      const result = await base.request<RefreshResponse>({
        method: 'POST',
        path: REFRESH_PATH,
        body: { refresh },
        auth: false,
      })
      // Rotation: the submitted refresh token is now blacklisted. Store the
      // replacement before anything else can use the old one.
      options.setTokens(result)
      return result.access
    })()
    try {
      return await inFlight
    } finally {
      inFlight = null
    }
  }

  return {
    async request<T>(spec: RequestSpec): Promise<T> {
      const authenticated = spec.auth !== false
      try {
        return await base.request<T>(spec)
      } catch (error) {
        /*
         * Only 401 (malformed or expired token) is refreshable. The backend
         * rejects a token that was not minted by the admin login with
         * 403 `admin_scope_required`, and no amount of refreshing will add
         * the `scope: "admin"` claim to it — retrying would just burn a
         * refresh token and loop. Tear the session down instead.
         */
        if (error instanceof ApiError && error.isUnrecoverableAuth) {
          options.onSessionLost()
          throw error
        }

        const retryable =
          authenticated && error instanceof ApiError && error.status === 401 && spec.path !== REFRESH_PATH
        if (!retryable) throw error

        try {
          await refreshOnce()
        } catch {
          options.onSessionLost()
          throw error
        }

        // The single permitted retry. A 401 here is terminal.
        try {
          return await base.request<T>(spec)
        } catch (retryError) {
          if (
            retryError instanceof ApiError &&
            (retryError.status === 401 || retryError.isUnrecoverableAuth)
          ) {
            options.onSessionLost()
          }
          throw retryError
        }
      }
    },
  }
}
