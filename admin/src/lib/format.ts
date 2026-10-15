/** Display formatting. Pure, locale-aware, and tolerant of nulls. */

const numberFormat = new Intl.NumberFormat(undefined)
const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const dateFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
})
const dayLabelFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short' })

export const EM_DASH = '—'

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH
  return numberFormat.format(value)
}

/** Binary units, the convention the proxy pipeline already uses. */
export function formatBytes(value: number | null | undefined, fractionDigits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH
  if (value === 0) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  const exponent = Math.min(units.length - 1, Math.floor(Math.log(Math.abs(value)) / Math.log(1024)))
  const scaled = value / 1024 ** exponent
  const digits = exponent === 0 ? 0 : fractionDigits
  return `${scaled.toFixed(digits)} ${units[exponent]}`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? EM_DASH : dateTimeFormat.format(date)
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? EM_DASH : dateFormat.format(date)
}

export function formatDayLabel(date: Date): string {
  return dayLabelFormat.format(date)
}

const RELATIVE_STEPS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 7],
  ['week', 4.348],
  ['month', 12],
  ['year', Number.POSITIVE_INFINITY],
]

const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** "3 hours ago" / "in 2 days". Falls back to an em dash on null. */
export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return EM_DASH
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return EM_DASH

  let delta = (date.getTime() - now.getTime()) / 1000
  for (const [unit, step] of RELATIVE_STEPS) {
    if (Math.abs(delta) < step) return relativeFormat.format(Math.round(delta), unit)
    delta /= step
  }
  return dateFormat.format(date)
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return EM_DASH
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`
}

/**
 * DRF serialises DecimalField to a string by default, but a project that sets
 * `COERCE_DECIMAL_TO_STRING = False` sends a number. Accept both.
 */
export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatPrice(value: string | number | null | undefined, currency = 'USD'): string {
  const amount = toNumber(value)
  if (amount === null) return EM_DASH
  if (amount === 0) return 'Free'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount)
}

export function formatPercent(value: number, fractionDigits = 0): string {
  if (!Number.isFinite(value)) return EM_DASH
  return `${(value * 100).toFixed(fractionDigits)}%`
}

/** `0` when the denominator is zero, so a share never renders as NaN. */
export function share(part: number, total: number): number {
  return total > 0 ? part / total : 0
}

export function fullName(user: { first_name: string; last_name: string; email: string }): string {
  const name = `${user.first_name} ${user.last_name}`.trim()
  return name === '' ? user.email : name
}

export function initials(user: { first_name: string; last_name: string; email: string }): string {
  const first = user.first_name.trim()[0]
  const last = user.last_name.trim()[0]
  if (first && last) return `${first}${last}`.toUpperCase()
  return (user.email.trim()[0] ?? '?').toUpperCase()
}

/** ISO date at UTC midnight, for day bucketing. */
export function dayKey(iso: string): string {
  return iso.slice(0, 10)
}

export function pluralise(count: number, singular: string, plural = `${singular}s`): string {
  return count === 1 ? singular : plural
}
