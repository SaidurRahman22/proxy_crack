/**
 * JWT custody.
 *
 * The access token lives in a module-local variable and nowhere else — not
 * localStorage, not sessionStorage, not a cookie. A page reload therefore
 * starts with no access token and silently mints a new one from the refresh
 * token, which is the intended behaviour.
 *
 * The refresh token is persisted to localStorage. That is an accepted v1
 * trade-off (it buys session continuity across reloads at the cost of XSS
 * exposure); the access token never joins it.
 */

const REFRESH_KEY = 'pc.admin.refresh'

let accessToken: string | null = null

/** localStorage throws in private modes and sandboxed frames. Never fatal. */
function safeRead(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeWrite(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    /* Session degrades to this tab only. Acceptable. */
  }
}

export const tokenStore = {
  getAccess(): string | null {
    return accessToken
  },

  setAccess(token: string | null): void {
    accessToken = token
  },

  getRefresh(): string | null {
    return safeRead(REFRESH_KEY)
  },

  setRefresh(token: string | null): void {
    safeWrite(REFRESH_KEY, token)
  },

  /** Wipes both halves. Called on logout and on an unrecoverable 401. */
  clear(): void {
    accessToken = null
    safeWrite(REFRESH_KEY, null)
  },

  /** True when a session is at least plausible — used to gate the boot probe. */
  hasSession(): boolean {
    return accessToken !== null || safeRead(REFRESH_KEY) !== null
  },
}
