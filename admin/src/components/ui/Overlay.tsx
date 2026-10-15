import { X } from 'lucide-react'
import { useCallback, useId, useRef, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { useFocusTrap } from '@/hooks/useFocusTrap'
import { cn } from '@/lib/cn'

import { Button } from './Button'

interface OverlayProps {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
  /** `dialog` centres; `drawer` slides in from the right. */
  kind?: 'dialog' | 'drawer'
  wide?: boolean
  /** Suppress close-on-backdrop while a mutation is in flight. */
  busy?: boolean
}

/**
 * The single modal surface in the app — dialogs and side drawers are the same
 * component with a different anchor. Focus is trapped, Escape closes, the
 * backdrop closes, focus returns to the trigger, and the title labels the
 * dialog for assistive technology.
 */
export function Overlay({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  kind = 'dialog',
  wide = false,
  busy = false,
}: OverlayProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const subtitleId = `${titleId}-sub`

  const dismiss = useCallback(() => {
    if (!busy) onClose()
  }, [busy, onClose])

  useFocusTrap(panelRef, open, dismiss)

  if (!open) return null

  const onBackdrop = (event: MouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) dismiss()
  }

  return createPortal(
    <div
      className={cn('overlay', kind === 'drawer' ? 'overlay--right' : 'overlay--center')}
      onMouseDown={onBackdrop}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? subtitleId : undefined}
        className={cn(kind === 'drawer' ? 'drawer' : 'dialog', wide && 'drawer--wide')}
      >
        <div className={kind === 'drawer' ? 'drawer__header' : 'dialog__header'}>
          <div>
            <h2 className={kind === 'drawer' ? 'drawer__title' : 'dialog__title'} id={titleId}>
              {title}
            </h2>
            {subtitle && (
              <p className={kind === 'drawer' ? 'drawer__subtitle' : 'dialog__subtitle'} id={subtitleId}>
                {subtitle}
              </p>
            )}
          </div>
          <Button variant="quiet" size="sm" iconOnly icon={<X size={16} />} onClick={dismiss}>
            Close
          </Button>
        </div>

        <div className={kind === 'drawer' ? 'drawer__body' : 'dialog__body'}>{children}</div>

        {footer && <div className={kind === 'drawer' ? 'drawer__footer' : 'dialog__footer'}>{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}
