/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the Django admin API, no trailing slash. */
  readonly VITE_API_BASE_URL?: string
  /** `"true"` swaps the HTTP transport for the in-memory contract mock. */
  readonly VITE_USE_MOCK_API?: string
  /** Artificial latency applied to mock responses, in ms. */
  readonly VITE_MOCK_LATENCY_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
