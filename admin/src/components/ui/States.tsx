import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import { describeError } from '@/api/errors'
import { cn } from '@/lib/cn'

import { Button } from './Button'

export function LoadingState({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner spinner--lg" aria-hidden="true" />
      <p className="state__body">{label}…</p>
    </div>
  )
}

export function EmptyState({
  title,
  body,
  icon,
  action,
}: {
  title: string
  body?: string
  icon?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="state">
      <span className="state__icon" aria-hidden="true">
        {icon ?? <Inbox size={20} />}
      </span>
      <p className="state__title">{title}</p>
      {body && <p className="state__body">{body}</p>}
      {action && <div className="state__actions">{action}</div>}
    </div>
  )
}

export function ErrorState({
  error,
  title = 'Could not load this',
  onRetry,
}: {
  error: unknown
  title?: string
  onRetry?: () => void
}) {
  return (
    <div className="state" role="alert">
      <span className="state__icon state__icon--danger" aria-hidden="true">
        <AlertTriangle size={20} />
      </span>
      <p className="state__title">{title}</p>
      <p className="state__body danger-text">{describeError(error)}</p>
      {onRetry && (
        <div className="state__actions">
          <Button variant="secondary" icon={<RefreshCw size={14} />} onClick={onRetry}>
            Try again
          </Button>
        </div>
      )}
    </div>
  )
}

export type BannerTone = 'danger' | 'warning' | 'info' | 'accent'

export function Banner({
  tone,
  icon,
  children,
  role,
}: {
  tone: BannerTone
  icon?: ReactNode
  children: ReactNode
  role?: 'alert' | 'status'
}) {
  return (
    <div className={cn('banner', `banner--${tone}`)} role={role}>
      {icon}
      <div className="banner__body">{children}</div>
    </div>
  )
}

/** Error summary above a form, for failures that are not field-shaped. */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <Banner tone="danger" role="alert" icon={<AlertTriangle size={15} aria-hidden="true" />}>
      {message}
    </Banner>
  )
}

export function SkeletonRows({ rows, columns }: { rows: number; columns: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, rowIndex) => (
        <tr key={rowIndex} aria-hidden="true">
          {Array.from({ length: columns }, (_, columnIndex) => (
            <td key={columnIndex}>
              <span
                className="skeleton"
                style={{ width: `${45 + ((rowIndex * 7 + columnIndex * 23) % 50)}%` }}
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}
