import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/cn'

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'ghost'
  | 'quiet'
  | 'danger'
  | 'danger-ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md' | 'lg'
  block?: boolean
  /** Renders a spinner in place of the leading icon and disables the button. */
  loading?: boolean
  icon?: ReactNode
  /** Icon-only: supply `aria-label`, which is enforced by the prop type. */
  iconOnly?: boolean
}

export function Button({
  variant = 'ghost',
  size = 'md',
  block = false,
  loading = false,
  icon,
  iconOnly = false,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'btn',
        `btn--${variant}`,
        size !== 'md' && `btn--${size}`,
        block && 'btn--block',
        iconOnly && 'btn--icon',
        className,
      )}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="spinner" aria-hidden="true" /> : icon}
      {!iconOnly && children}
      {iconOnly && <span className="visually-hidden">{children}</span>}
    </button>
  )
}
