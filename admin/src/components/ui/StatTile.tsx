import { ArrowUpRight, Minus } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'
import { formatNumber } from '@/lib/format'

interface StatTileProps {
  label: string
  value: ReactNode
  unit?: string
  hero?: boolean
  icon?: ReactNode
  /** A 7-day change, rendered as a chip rather than a second number. */
  delta?: { value: number; label: string }
  children?: ReactNode
}

export function StatTile({ label, value, unit, hero, icon, delta, children }: StatTileProps) {
  return (
    <section className="card">
      <div className="stat">
        <h3 className="stat__label">
          {icon}
          {label}
        </h3>
        <div className="stat__figure">
          <span className={cn('stat__value', hero && 'stat__value--hero')}>{value}</span>
          {unit && <span className="stat__unit">{unit}</span>}
          {delta && (
            <span className={cn('stat__delta', delta.value > 0 ? 'stat__delta--up' : 'stat__delta--flat')}>
              {delta.value > 0 ? (
                <ArrowUpRight size={12} aria-hidden="true" />
              ) : (
                <Minus size={12} aria-hidden="true" />
              )}
              {delta.value > 0 ? `+${formatNumber(delta.value)}` : formatNumber(delta.value)}{' '}
              {delta.label}
            </span>
          )}
        </div>
        {children && <div className="stat__foot">{children}</div>}
      </div>
    </section>
  )
}

export function StatSplit({
  items,
}: {
  items: Array<{ key: string; label: string; value: ReactNode }>
}) {
  return (
    <div className="stat__split">
      {items.map((item) => (
        <div className="stat__split-item" key={item.key}>
          <span className="stat__split-value">{item.value}</span>
          <span className="stat__split-label">{item.label}</span>
        </div>
      ))}
    </div>
  )
}
