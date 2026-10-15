/**
 * Runtime configuration.
 *
 * The API base URL is never hardcoded at a call site — every request goes
 * through the transport, which reads it from here exactly once.
 */

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

function readNumber(value: string | undefined, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export const env = {
  /** No trailing slash — paths in the API layer all start with one. */
  apiBaseUrl: (import.meta.env.VITE_API_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, ''),

  /**
   * The single switch between the in-memory contract mock and the real Django
   * API. Nothing above the transport layer knows which one is in play.
   */
  useMockApi: import.meta.env.VITE_USE_MOCK_API === 'true',

  mockLatencyMs: readNumber(import.meta.env.VITE_MOCK_LATENCY_MS, 320),
} as const
