import { useMemo, useRef, useState } from 'react'

import { useElementWidth } from '@/hooks/useElementWidth'
import { formatDayLabel } from '@/lib/format'

export interface SeriesPoint {
  /** Day bucket, UTC midnight. */
  date: Date
  value: number
}

export interface Series {
  key: string
  label: string
  color: string
  points: SeriesPoint[]
}

interface TimeSeriesChartProps {
  series: Series[]
  /** Both series must share a scale — this chart never draws a second axis. */
  formatValue: (value: number) => string
  height?: number
  /** Draw a translucent area under each line. Off for sparse/overlapping sets. */
  area?: boolean
  ariaLabel: string
}

const PADDING = { top: 12, right: 10, bottom: 22, left: 46 }

function niceCeiling(value: number): number {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  const normalised = value / magnitude
  const step = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10
  return step * magnitude
}

/**
 * Multi-series line/area chart on one shared y scale.
 *
 * Deliberately not a dual-axis chart: two measures with different scales get
 * two charts or a common index, never two axes on one frame. Hover is a
 * first-class feature here — crosshair, per-series markers and a tooltip —
 * and the same data is exposed as a visually-hidden table so the chart is not
 * a dead end for assistive technology.
 */
export function TimeSeriesChart({
  series,
  formatValue,
  height = 190,
  area = true,
  ariaLabel,
}: TimeSeriesChartProps) {
  const frameRef = useRef<HTMLDivElement>(null)
  const width = useElementWidth(frameRef, 640)
  const [hovered, setHovered] = useState<number | null>(null)

  const length = series[0]?.points.length ?? 0

  const geometry = useMemo(() => {
    const max = niceCeiling(
      Math.max(1, ...series.flatMap((line) => line.points.map((point) => point.value))),
    )
    const plotWidth = Math.max(1, width - PADDING.left - PADDING.right)
    const plotHeight = Math.max(1, height - PADDING.top - PADDING.bottom)
    const stepX = length > 1 ? plotWidth / (length - 1) : 0

    const x = (index: number): number => PADDING.left + index * stepX
    const y = (value: number): number => PADDING.top + plotHeight * (1 - value / max)

    return { max, plotWidth, plotHeight, x, y, baseline: PADDING.top + plotHeight }
  }, [series, width, height, length])

  if (length === 0) return null

  const ticks = [0, 0.5, 1].map((fraction) => geometry.max * fraction)

  const pointerIndex = (clientX: number): number => {
    const frame = frameRef.current
    if (!frame || length < 2) return 0
    const rect = frame.getBoundingClientRect()
    const relative = clientX - rect.left - PADDING.left
    const index = Math.round((relative / geometry.plotWidth) * (length - 1))
    return Math.min(length - 1, Math.max(0, index))
  }

  const activeDate = hovered !== null ? series[0]?.points[hovered]?.date : undefined

  return (
    <div className="chart-frame" ref={frameRef}>
      <svg
        className="chart"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel}
      >
        {/* Recessive grid — structure, not content. */}
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              className="chart__grid"
              x1={PADDING.left}
              x2={width - PADDING.right}
              y1={geometry.y(tick)}
              y2={geometry.y(tick)}
            />
            <text
              className="chart__axis-label"
              x={PADDING.left - 8}
              y={geometry.y(tick)}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {formatValue(tick)}
            </text>
          </g>
        ))}

        {series.map((line) => {
          const path = line.points
            .map((point, index) => `${index === 0 ? 'M' : 'L'}${geometry.x(index)},${geometry.y(point.value)}`)
            .join(' ')
          const areaPath = `${path} L${geometry.x(length - 1)},${geometry.baseline} L${geometry.x(0)},${geometry.baseline} Z`
          return (
            <g key={line.key}>
              {area && <path d={areaPath} fill={line.color} fillOpacity={0.1} />}
              <path className="chart__line" d={path} stroke={line.color} />
            </g>
          )
        })}

        {/* X labels: every other day, so they never collide. */}
        {series[0]?.points.map((point, index) =>
          index % 2 === 0 || index === length - 1 ? (
            <text
              key={point.date.toISOString()}
              className="chart__axis-label"
              x={geometry.x(index)}
              y={height - 6}
              textAnchor={index === 0 ? 'start' : index === length - 1 ? 'end' : 'middle'}
            >
              {formatDayLabel(point.date)}
            </text>
          ) : null,
        )}

        {hovered !== null && (
          <>
            <line
              className="chart__crosshair"
              x1={geometry.x(hovered)}
              x2={geometry.x(hovered)}
              y1={PADDING.top}
              y2={geometry.baseline}
            />
            {series.map((line) => {
              const point = line.points[hovered]
              if (!point) return null
              return (
                <circle
                  key={line.key}
                  className="chart__marker"
                  cx={geometry.x(hovered)}
                  cy={geometry.y(point.value)}
                  r={4.5}
                  fill={line.color}
                />
              )
            })}
          </>
        )}

        {/* One generous hit area — larger than the marks, as it should be. */}
        <rect
          className="chart__hit"
          x={0}
          y={0}
          width={width}
          height={height}
          onMouseMove={(event) => setHovered(pointerIndex(event.clientX))}
          onMouseLeave={() => setHovered(null)}
        />
      </svg>

      {hovered !== null && activeDate && (
        <div
          className="chart-tooltip"
          style={{
            left: Math.min(Math.max(geometry.x(hovered), 70), width - 70),
            top: PADDING.top - 6,
          }}
        >
          <div className="chart-tooltip__title">{formatDayLabel(activeDate)}</div>
          {series.map((line) => (
            <div className="chart-tooltip__row" key={line.key}>
              <span className="chart-tooltip__swatch" style={{ background: line.color }} />
              <span>{line.label}</span>
              <span className="chart-tooltip__value">
                {formatValue(line.points[hovered]?.value ?? 0)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Screen-reader equivalent of the plot. */}
      <table className="visually-hidden">
        <caption>{ariaLabel}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            {series.map((line) => (
              <th scope="col" key={line.key}>
                {line.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {series[0]?.points.map((point, index) => (
            <tr key={point.date.toISOString()}>
              <th scope="row">{point.date.toDateString()}</th>
              {series.map((line) => (
                <td key={line.key}>{formatValue(line.points[index]?.value ?? 0)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ChartLegend({ series }: { series: Array<{ key: string; label: string; color: string }> }) {
  // A single series needs no legend — the title names it.
  if (series.length < 2) return null
  return (
    <div className="meter-legend">
      {series.map((line) => (
        <span className="meter-legend__item" key={line.key}>
          <span className="meter-legend__swatch" style={{ background: line.color }} />
          {line.label}
        </span>
      ))}
    </div>
  )
}
