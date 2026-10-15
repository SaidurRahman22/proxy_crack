import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'

import { ApiError } from './api/errors'
import { AuthProvider } from './auth/AuthProvider'
import { LoginPage } from './auth/LoginPage'
import { RedirectIfAuthenticated, RequireAuth } from './auth/RequireAuth'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AppShell } from './components/layout/AppShell'
import { ConfirmProvider } from './components/ui/ConfirmProvider'
import { EmptyState, LoadingState } from './components/ui/States'
import { ToastProvider } from './components/ui/ToastProvider'
import { DashboardPage } from './pages/DashboardPage'

/*
 * Route-level code splitting. The dashboard is eager because it is the landing
 * screen; everything else loads on navigation, which keeps the first paint to
 * the shell plus one page.
 */
const UsersPage = lazy(() => import('./pages/users/UsersPage').then((m) => ({ default: m.UsersPage })))
const PlansPage = lazy(() => import('./pages/plans/PlansPage').then((m) => ({ default: m.PlansPage })))
const SubscriptionsPage = lazy(() =>
  import('./pages/subscriptions/SubscriptionsPage').then((m) => ({ default: m.SubscriptionsPage })),
)
const ProxiesPage = lazy(() =>
  import('./pages/proxies/ProxiesPage').then((m) => ({ default: m.ProxiesPage })),
)
const ContentPage = lazy(() =>
  import('./pages/content/ContentPage').then((m) => ({ default: m.ContentPage })),
)
const CsvUploadPage = lazy(() =>
  import('./pages/uploads/CsvUploadPage').then((m) => ({ default: m.CsvUploadPage })),
)
const UploadBatchesPage = lazy(() =>
  import('./pages/uploads/UploadBatchesPage').then((m) => ({ default: m.UploadBatchesPage })),
)
const UsageLogsPage = lazy(() =>
  import('./pages/usage/UsageLogsPage').then((m) => ({ default: m.UsageLogsPage })),
)
const ChainsPage = lazy(() => import('./pages/chains/ChainsPage').then((m) => ({ default: m.ChainsPage })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // 4xx is a decision, not a blip. Only retry transport-level failures,
        // and never fight the refresh-on-401 path.
        if (error instanceof ApiError) return false
        return failureCount < 2
      },
    },
    mutations: { retry: false },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <ConfirmProvider>
            <AuthProvider>
              <ErrorBoundary>
                <Suspense fallback={<LoadingState label="Loading screen" />}>
                  <Routes>
                    <Route
                      path="/login"
                      element={
                        <RedirectIfAuthenticated>
                          <LoginPage />
                        </RedirectIfAuthenticated>
                      }
                    />

                    <Route element={<RequireAuth />}>
                      <Route element={<AppShell />}>
                        <Route index element={<DashboardPage />} />
                        <Route path="users" element={<UsersPage />} />
                        <Route path="plans" element={<PlansPage />} />
                        <Route path="subscriptions" element={<SubscriptionsPage />} />
                        <Route path="proxies" element={<ProxiesPage />} />
                        <Route path="content" element={<ContentPage />} />
                        <Route path="upload" element={<CsvUploadPage />} />
                        <Route path="upload-batches" element={<UploadBatchesPage />} />
                        <Route path="usage-logs" element={<UsageLogsPage />} />
                        <Route path="chains" element={<ChainsPage />} />
                        <Route path="*" element={<NotFound />} />
                      </Route>
                    </Route>
                  </Routes>
                </Suspense>
              </ErrorBoundary>
            </AuthProvider>
          </ConfirmProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

function NotFound() {
  return (
    <EmptyState
      title="No such screen"
      body="The address you followed does not match any section of the console."
      action={
        <Link className="btn btn--secondary" to="/">
          Back to the dashboard
        </Link>
      }
    />
  )
}
