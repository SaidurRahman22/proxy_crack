import {
  CheckCircle2,
  CircleDashed,
  CircleSlash2,
  Clock,
  Power,
  PowerOff,
  XCircle,
} from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'
import type { Protocol, SubscriptionStatus } from '@/api/types'

/* ------------------------------------------------------------------ *
 * Badge
 * ------------------------------------------------------------------ */

export type BadgeTone = 'neutral' | 'accent' | 'danger' | 'warning' | 'info' | 'outline'

export function Badge({
  tone = 'neutral',
  icon,
  children,
}: {
  tone?: BadgeTone
  icon?: ReactNode
  children: ReactNode
}) {
  return (
    <span className={cn('badge', `badge--${tone}`)}>
      {icon}
      {children}
    </span>
  )
}

/* ------------------------------------------------------------------ *
 * Status
 *
 * Every indicator below pairs an ICON with a TEXT LABEL. Colour is
 * reinforcement, never the carrier — the green/red alive/dead pair is the
 * classic deuteranopia failure (dE ~4 under simulation against ~34 for normal
 * vision) and proxy health is the signal an operator acts on most.
 * ------------------------------------------------------------------ */

const ICON = 13

export function AliveStatus({ alive }: { alive: boolean }) {
  return alive ? (
    <span className="status status--alive">
      <CheckCircle2 size={ICON} className="status__icon" aria-hidden="true" />
      Alive
    </span>
  ) : (
    <span className="status status--dead">
      <XCircle size={ICON} className="status__icon" aria-hidden="true" />
      Dead
    </span>
  )
}

export function EnabledStatus({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <span className="status status--alive">
      <Power size={ICON} className="status__icon" aria-hidden="true" />
      Enabled
    </span>
  ) : (
    <span className="status status--muted">
      <PowerOff size={ICON} className="status__icon" aria-hidden="true" />
      Disabled
    </span>
  )
}

export function ActiveStatus({ active }: { active: boolean }) {
  return active ? (
    <span className="status status--alive">
      <CheckCircle2 size={ICON} className="status__icon" aria-hidden="true" />
      Active
    </span>
  ) : (
    <span className="status status--muted">
      <CircleSlash2 size={ICON} className="status__icon" aria-hidden="true" />
      Inactive
    </span>
  )
}

export function PublishedStatus({ published }: { published: boolean }) {
  return published ? (
    <span className="status status--alive">
      <CheckCircle2 size={ICON} className="status__icon" aria-hidden="true" />
      Published
    </span>
  ) : (
    <span className="status status--muted">
      <CircleDashed size={ICON} className="status__icon" aria-hidden="true" />
      Draft
    </span>
  )
}

const SUBSCRIPTION_PRESENTATION: Record<
  SubscriptionStatus,
  { tone: string; label: string; Icon: typeof CheckCircle2 }
> = {
  active: { tone: 'status--alive', label: 'Active', Icon: CheckCircle2 },
  expired: { tone: 'status--dead', label: 'Expired', Icon: XCircle },
  cancelled: { tone: 'status--muted', label: 'Cancelled', Icon: CircleSlash2 },
  pending: { tone: 'status--warning', label: 'Pending', Icon: Clock },
}

export function SubscriptionStatusTag({ status }: { status: SubscriptionStatus }) {
  const presentation = SUBSCRIPTION_PRESENTATION[status] ?? {
    tone: 'status--muted',
    label: status,
    Icon: CircleDashed,
  }
  const { tone, label, Icon } = presentation
  return (
    <span className={cn('status', tone)}>
      <Icon size={ICON} className="status__icon" aria-hidden="true" />
      {label}
    </span>
  )
}

/* ------------------------------------------------------------------ *
 * Protocol chip — validated categorical palette, fixed assignment
 * ------------------------------------------------------------------ */

const PROTOCOL_LABEL: Record<Protocol, string> = {
  ss: 'ss',
  socks5: 'socks5',
  http: 'http',
}

export function ProtocolChip({ protocol }: { protocol: Protocol }) {
  const known = protocol in PROTOCOL_LABEL
  return (
    <span className={cn('proto', known && `proto--${protocol}`)}>
      {PROTOCOL_LABEL[protocol] ?? protocol}
    </span>
  )
}

export function TierBadge({ tier }: { tier: string }) {
  return <Badge tone={tier === 'premium' ? 'info' : 'outline'}>{tier}</Badge>
}
