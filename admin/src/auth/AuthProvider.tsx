import { useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { api, onSessionLost } from '@/api/client'
import { tokenStore } from '@/api/tokenStore'
import type { AdminUser } from '@/api/types'

type AuthStatus = 'booting' | 'authenticated' | 'anonymous'

interface AuthContextValue {
  status: AuthStatus
  user: AdminUser | null
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** Client-side only. The server is the authority on every request. */
  isSuperuser: boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

/**
 * Session state.
 *
 * On boot, if a refresh token exists, the app probes `/auth/me/`. The access
 * token is gone after a reload, so that probe 401s, the transport refreshes
 * once and retries — which is exactly the path a long-idle tab takes, so it is
 * exercised on every reload rather than only in theory.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() =>
    tokenStore.hasSession() ? 'booting' : 'anonymous',
  )
  const [user, setUser] = useState<AdminUser | null>(null)
  const queryClient = useQueryClient()
  const booted = useRef(false)

  // The transport calls this when a refresh fails or the single retry still 401s.
  useEffect(() => {
    onSessionLost(() => {
      setUser(null)
      setStatus('anonymous')
      queryClient.clear()
    })
  }, [queryClient])

  useEffect(() => {
    // The ref guard keeps this to one request under StrictMode's double
    // invocation. There is deliberately no AbortController and no cleanup:
    // aborting on StrictMode's synthetic unmount would reject the probe, and
    // the guard would then stop the second run from retrying — which silently
    // logged every operator out on reload.
    if (booted.current) return
    booted.current = true
    if (!tokenStore.hasSession()) return

    api.auth
      .me()
      .then((me) => {
        setUser(me)
        setStatus('authenticated')
      })
      .catch(() => {
        tokenStore.clear()
        setUser(null)
        setStatus('anonymous')
      })
  }, [])

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await api.auth.login({ email, password })
      tokenStore.setAccess(result.access)
      tokenStore.setRefresh(result.refresh)
      setUser(result.user)
      setStatus('authenticated')
      // Anything cached from a previous operator must not leak into this one.
      queryClient.clear()
    },
    [queryClient],
  )

  const logout = useCallback(async () => {
    const refresh = tokenStore.getRefresh()
    // Blacklisting is best-effort: a failed logout must still log you out here.
    if (refresh) {
      try {
        await api.auth.logout(refresh)
      } catch {
        /* ignored by design */
      }
    }
    tokenStore.clear()
    setUser(null)
    setStatus('anonymous')
    queryClient.clear()
  }, [queryClient])

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, login, logout, isSuperuser: user?.is_superuser ?? false }),
    [status, user, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
