import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { SeriesPoint } from '@/components/charts/TimeSeriesChart'

/**
 * Daily series derived from list endpoints.
 *
 * `/api/admin/stats/` gives 7-day *totals* (`bytes_up_7d`, `sessions_7d`,
 * `new_7d`) but no per-day breakdown, so the shape has to come from the
 * list endpoints the contract does define. That works at this scale and is
 * honest about its limits — each hook reports `truncated` when the window
 * holds more rows than it fetched, and the card says so on screen.
 *
 * The right long-term fix is a daily series on `/stats/`; this is flagged in
 * the hand-off notes rather than invented here.
 */

const MAX_PAGE = 200
const MAX_PAGES = 3

function emptyDays(count: number): Map<string, number> {
  const buckets = new Map<string, number>()
  const today = new Date()
  today.setUTCHours(0, 0, 0, 0)
  for (let offset = count - 1; offset >= 0; offset -= 1) {
    const day = new Date(today.getTime() - offset * 86400000)
    buckets.set(day.toISOString().slice(0, 10), 0)
  }
  return buckets
}

function toPoints(buckets: Map<string, number>): SeriesPoint[] {
  return [...buckets.entries()].map(([key, value]) => ({
    date: new Date(`${key}T00:00:00.000Z`),
    value,
  }))
}

export interface BandwidthSeries {
  up: SeriesPoint[]
  down: SeriesPoint[]
  truncated: boolean
  sampled: number
  total: number
}

/** Up/down bytes per day over the last `days`, bucketed from usage logs. */
export function useBandwidthSeries(days = 7): UseQueryResult<BandwidthSeries> {
  return useQuery({
    queryKey: ['derived', 'bandwidth', days],
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const after = new Date(Date.now() - days * 86400000)
      after.setUTCHours(0, 0, 0, 0)
      const params = {
        session_start_after: after.toISOString(),
        ordering: '-session_start',
        page_size: MAX_PAGE,
      }

      const first = await api.usageLogs.list({ ...params, page: 1 }, signal)
      const pages = [first]
      const pageCount = Math.min(MAX_PAGES, Math.ceil(first.count / MAX_PAGE))
      for (let page = 2; page <= pageCount; page += 1) {
        pages.push(await api.usageLogs.list({ ...params, page }, signal))
      }

      const up = emptyDays(days)
      const down = emptyDays(days)
      let sampled = 0
      for (const page of pages) {
        for (const log of page.results) {
          const key = log.session_start.slice(0, 10)
          if (!up.has(key)) continue
          up.set(key, (up.get(key) ?? 0) + log.bytes_up)
          down.set(key, (down.get(key) ?? 0) + log.bytes_down)
          sampled += 1
        }
      }

      return {
        up: toPoints(up),
        down: toPoints(down),
        truncated: first.count > MAX_PAGE * MAX_PAGES,
        sampled,
        total: first.count,
      }
    },
  })
}

export interface SignupSeries {
  points: SeriesPoint[]
  truncated: boolean
}

/** Signups per day, bucketed from `/users/?ordering=-date_joined`. */
export function useSignupSeries(days = 14): UseQueryResult<SignupSeries> {
  return useQuery({
    queryKey: ['derived', 'signups', days],
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const page = await api.users.list(
        { ordering: '-date_joined', page_size: MAX_PAGE, page: 1 },
        signal,
      )

      const buckets = emptyDays(days)
      const windowStart = new Date(Date.now() - days * 86400000)
      windowStart.setUTCHours(0, 0, 0, 0)

      for (const user of page.results) {
        const key = user.date_joined.slice(0, 10)
        if (buckets.has(key)) buckets.set(key, (buckets.get(key) ?? 0) + 1)
      }

      // If the oldest row we fetched is still inside the window, there are
      // older signups in it that we never saw.
      const oldest = page.results[page.results.length - 1]?.date_joined
      const truncated =
        page.count > MAX_PAGE && oldest !== undefined && oldest > windowStart.toISOString()

      return { points: toPoints(buckets), truncated }
    },
  })
}
