import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from './AuthProvider'

/**
 * Route guard.
 *
 * The shell is never mounted for an unauthenticated visitor — not hidden,
 * not rendered-then-redirected. While the boot probe runs we render a neutral
 * splash rather than the login form, so a valid session does not flash a
 * login screen on every reload.
 */
export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'booting') {
    return (
      <div
        className="state"
        style={{ minHeight: '100vh' }}
        role="status"
        aria-live="polite"
      >
        <span className="spinner spinner--lg" aria-hidden="true" />
        <p className="state__body">Restoring session…</p>
      </div>
    )
  }

  if (status === 'anonymous') {
    // `from` lets the login screen send the operator back where they were.
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />
}

/**
 * Keeps an already-authenticated operator off the login screen, and honours
 * the `from` location so a deep link survives a cold load that briefly
 * bounced through `/login`.
 */
export function RedirectIfAuthenticated({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'authenticated') {
    const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname
    return <Navigate to={from ?? '/'} replace />
  }
  return <>{children}</>
}
