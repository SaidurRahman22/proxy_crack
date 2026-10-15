import type { ApiErrorEnvelope, ErrorDetail } from './types'
import { ERROR_CODES } from './types'

/**
 * A structured failure from the admin API, carrying the
 * `{"error": {"code", "status", "detail"}}` envelope intact so forms can map
 * `detail` back onto their fields.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: ErrorDetail
  /**
   * Code-specific members carried alongside `{code, status, detail}` —
   * `active_subscriptions` on a plan guard, `proxy_ids` on a refused bulk
   * delete. Preserved so a screen can name what blocked the operation.
   */
  readonly extra: Record<string, unknown>
  /** Parsed from `Retry-After` on a 429. */
  readonly retryAfterSeconds: number | null

  constructor(
    status: number,
    code: string,
    detail: ErrorDetail,
    extra: Record<string, unknown> = {},
    retryAfterSeconds: number | null = null,
  ) {
    super(summarise(detail, status, code, retryAfterSeconds))
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
    this.extra = extra
    this.retryAfterSeconds = retryAfterSeconds
  }

  is(code: string): boolean {
    return this.code === code
  }

  /** A numeric member of the error object, e.g. `active_subscriptions`. */
  number(key: string): number | null {
    const value = this.extra[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : null
  }

  /** A numeric-array member, e.g. `proxy_ids`. */
  numbers(key: string): number[] {
    const value = this.extra[key]
    return Array.isArray(value) ? value.filter((v): v is number => typeof v === 'number') : []
  }

  /**
   * True when the token can never be made to work by refreshing — the
   * backend rejects a non-admin-scoped token with 403, not 401, and retrying
   * it is pointless.
   */
  get isUnrecoverableAuth(): boolean {
    return this.status === 403 && this.code === ERROR_CODES.adminScopeRequired
  }

  /**
   * `detail` flattened to `field -> message`, for inline form validation.
   * Empty when the failure was not field-shaped.
   */
  get fieldErrors(): Record<string, string> {
    if (typeof this.detail !== 'object' || this.detail === null || Array.isArray(this.detail)) {
      return {}
    }
    const out: Record<string, string> = {}
    for (const [field, value] of Object.entries(this.detail)) {
      out[field] = Array.isArray(value) ? value.join(' ') : value
    }
    return out
  }

  /** True when the envelope carried no per-field breakdown. */
  get isFormLevel(): boolean {
    return Object.keys(this.fieldErrors).length === 0
  }
}

/** The request never reached the API — DNS, CORS, offline, abort. */
export class NetworkError extends Error {
  override readonly cause: unknown

  constructor(message: string, cause?: unknown) {
    super(message)
    this.name = 'NetworkError'
    this.cause = cause
  }
}

function summarise(
  detail: ErrorDetail,
  status: number,
  code?: string,
  retryAfterSeconds?: number | null,
): string {
  /*
   * Rate limiting deserves a sentence an operator can act on, not "429".
   * Throttle scopes here are graded and deliberately tight (credential
   * reveals 120/hour and 400/day, bulk actions 20/hour, CSV import 10/hour),
   * so hitting one is a normal operational event, not a fault.
   *
   * DRF's own throttle message already names the wait, so prefer it and only
   * synthesise one when the body gave us nothing useful.
   */
  if (status === 429) {
    const wait =
      retryAfterSeconds && retryAfterSeconds > 0
        ? ` Try again in ${formatWait(retryAfterSeconds)}.`
        : ' Try again shortly.'
    if (typeof detail === 'string' && detail.trim() !== '') {
      return retryAfterSeconds && retryAfterSeconds > 0 ? `${detail}${wait}` : detail
    }
    return `Too many ${code && code !== 'error' ? `${code.replace(/_/g, ' ')} ` : ''}requests.${wait}`
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.join(' ')
  const parts = Object.entries(detail).map(
    ([field, value]) => `${field}: ${Array.isArray(value) ? value.join(' ') : value}`,
  )
  return parts.length > 0 ? parts.join(' · ') : `Request failed with status ${status}`
}

function formatWait(seconds: number): string {
  if (seconds < 90) return `${Math.ceil(seconds)} seconds`
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return `${minutes} minutes`
  return `${Math.round(minutes / 60)} hours`
}

function isEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false
  const inner: unknown = (value as { error: unknown }).error
  return typeof inner === 'object' && inner !== null && 'detail' in inner
}

/**
 * Build an {@link ApiError} from a parsed response body, tolerating the two
 * shapes DRF emits when a view bypasses the project's exception handler
 * (`{detail: "..."}` and a bare field map).
 */
export function toApiError(
  status: number,
  body: unknown,
  retryAfterSeconds: number | null = null,
): ApiError {
  if (isEnvelope(body)) {
    const { error } = body
    const { code: _code, status: _status, detail: _detail, ...extra } = error
    return new ApiError(
      typeof error.status === 'number' ? error.status : status,
      typeof error.code === 'string' ? error.code : 'error',
      error.detail,
      extra,
      retryAfterSeconds,
    )
  }
  if (typeof body === 'object' && body !== null) {
    const record = body as Record<string, unknown>
    if (typeof record.detail === 'string') {
      return new ApiError(status, 'error', record.detail, record, retryAfterSeconds)
    }
    const fields: Record<string, string | string[]> = {}
    for (const [key, value] of Object.entries(record)) {
      if (typeof value === 'string') fields[key] = value
      else if (Array.isArray(value) && value.every((v) => typeof v === 'string')) {
        fields[key] = value
      }
    }
    if (Object.keys(fields).length > 0) {
      return new ApiError(status, 'error', fields, record, retryAfterSeconds)
    }
  }
  if (typeof body === 'string' && body.trim() !== '') {
    return new ApiError(status, 'error', body.slice(0, 400), {}, retryAfterSeconds)
  }
  return new ApiError(
    status,
    'error',
    `Request failed with status ${status}.`,
    {},
    retryAfterSeconds,
  )
}

/** A human-facing sentence for any thrown value, for error surfaces. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof NetworkError) return error.message
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}
