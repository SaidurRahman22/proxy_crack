import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { ProxyCredentials } from '@/api/types'

interface CredentialState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  credentials: ProxyCredentials | null
  error: unknown
}

const IDLE: CredentialState = { status: 'idle', credentials: null, error: null }

/**
 * Fetches decrypted proxy credentials on explicit request.
 *
 * Deliberately NOT a `useQuery` hook. TanStack Query would put the plaintext
 * in a client-side cache that survives navigation, is shared across
 * components and is trivially readable from devtools. These values are the
 * sellable asset — the decryption is audit-logged server-side, so each reveal
 * must be one deliberate request.
 *
 * Rules enforced here:
 *   - component-local state only, never a cache, never storage
 *   - cleared when the proxy changes and on unmount
 *   - re-revealing re-requests, so the audit log reflects reality
 *
 * Disclosure goes through `POST /proxies/{id}/reveal/` so the audit log
 * records a deliberate act rather than a page view. That endpoint is being
 * added by the backend; until it lands, a 404/405 falls back once to the
 * detail GET, which returns the same fields. Delete `revealCredentials`'
 * fallback branch once `/reveal/` is deployed.
 */
export function useProxyCredentials(proxyId: number | null) {
  const [state, setState] = useState<CredentialState>(IDLE)
  const controller = useRef<AbortController | null>(null)

  const forget = useCallback(() => {
    controller.current?.abort()
    controller.current = null
    setState(IDLE)
  }, [])

  // A different proxy must never show the previous one's secrets.
  useEffect(() => {
    forget()
  }, [proxyId, forget])

  // And nothing survives the component.
  useEffect(() => forget, [forget])

  const reveal = useCallback(async () => {
    if (proxyId === null) return
    controller.current?.abort()
    const next = new AbortController()
    controller.current = next
    setState({ status: 'loading', credentials: null, error: null })
    try {
      const credentials = await revealCredentials(proxyId, next.signal)
      setState({ status: 'ready', credentials, error: null })
    } catch (error) {
      if (next.signal.aborted) return
      setState({ status: 'error', credentials: null, error })
    }
  }, [proxyId])

  return { ...state, reveal, forget }
}

async function revealCredentials(
  proxyId: number,
  signal: AbortSignal,
): Promise<ProxyCredentials> {
  try {
    return await api.proxies.reveal(proxyId, signal)
  } catch (error) {
    // Compatibility shim: the backend may not expose /reveal/ yet. Any other
    // failure — including the 429 throttle — propagates untouched.
    const notDeployed =
      error instanceof ApiError && (error.status === 404 || error.status === 405)
    if (!notDeployed) throw error

    const detail = await api.proxies.get(proxyId, signal)
    return {
      username: detail.username,
      password: detail.password,
      ss_method: detail.ss_method,
    }
  }
}
