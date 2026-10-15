import { Link } from 'react-router-dom'

import { formatNumber, formatPercent, share } from '@/lib/format'

export interface BarDatum {
  key: string
  label: string
  value: number
  color: string
  /** Optional drill-through — the bar becomes a link to a filtered list. */
  to?: string
}

/**
 * Horizontal bars, sorted by magnitude, direct-labelled.
 *
 * A horizontal bar beats a pie for a categorical breakdown: the labels read
 * left-to-right at any length and magnitude comparison is a single axis
 * instead of an angle judgement.
 */
export function HorizontalBars({ data, total }: { data: BarDatum[]; total: number }) {
  const sorted = [...data].sort((a, b) => b.value - a.value)
  const max = Math.max(1, ...sorted.map((item) => item.value))

  return (
    <div className="hbars">
      {sorted.map((item) => {
        const row = (
          <>
            <span className="hbar__label">{item.label}</span>
            <span className="hbar__track">
              <span
                className="hbar__fill"
                style={{ width: `${(item.value / max) * 100}%`, background: item.color }}
              />
            </span>
            <span className="hbar__value">
              {formatNumber(item.value)}
              <span className="hbar__share">{formatPercent(share(item.value, total))}</span>
            </span>
          </>
        )
        return item.to ? (
          <Link className="hbar" key={item.key} to={item.to} title={`Filter proxies by ${item.label}`}>
            {row}
          </Link>
        ) : (
          <div className="hbar" key={item.key}>
            {row}
          </div>
        )
      })}
    </div>
  )
}

export interface MeterSegment {
  key: string
  label: string
  value: number
  color: string
}

/**
 * A proportion bar. Two values do not need a chart — a stat tile with this
 * underneath says more, in less space, than a pie would.
 */
export function ProportionMeter({
  segments,
  tall = false,
  showLegend = true,
}: {
  segments: MeterSegment[]
  tall?: boolean
  showLegend?: boolean
}) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0)

  return (
    <div className="stack" style={{ gap: 'var(--space-xs)' }}>
      <div
        className={tall ? 'meter meter--tall' : 'meter'}
        role="img"
        aria-label={segments
          .map((segment) => `${segment.label}: ${formatNumber(segment.value)}`)
          .join(', ')}
      >
        {segments.map((segment) =>
          segment.value > 0 ? (
            <span
              key={segment.key}
              className="meter__segment"
              style={{
                width: `${share(segment.value, total) * 100}%`,
                background: segment.color,
              }}
            />
          ) : null,
        )}
      </div>
      {showLegend && (
        <div className="meter-legend">
          {segments.map((segment) => (
            <span className="meter-legend__item" key={segment.key}>
              <span className="meter-legend__swatch" style={{ background: segment.color }} />
              {segment.label}
              <span className="meter-legend__value">{formatNumber(segment.value)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
