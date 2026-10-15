import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from './ui/Button'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Last line of defence for render-time failures.
 *
 * Async failures are handled per-surface by TanStack Query error states; this
 * only catches the ones that would otherwise blank the screen.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled render error', error, info.componentStack)
  }

  override render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="state" style={{ minHeight: '60vh' }} role="alert">
        <span className="state__icon state__icon--danger" aria-hidden="true">
          <AlertTriangle size={20} />
        </span>
        <p className="state__title">This screen stopped responding</p>
        <p className="state__body danger-text">{error.message}</p>
        <div className="state__actions">
          <Button
            variant="secondary"
            icon={<RefreshCw size={14} />}
            onClick={() => window.location.reload()}
          >
            Reload the console
          </Button>
        </div>
      </div>
    )
  }
}
