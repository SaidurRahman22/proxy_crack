import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

export interface ListQueryState {
  page: number
  pageSize: number
  search: string
  ordering: string
  /** Resource-specific django-filter values, as strings. */
  filters: Record<string, string>
}

export interface ListQueryApi extends ListQueryState {
  setSearch: (value: string) => void
  setOrdering: (value: string) => void
  setPage: (value: number) => void
  setPageSize: (value: number) => void
  setFilter: (key: string, value: string) => void
  clearFilters: () => void
  /** True when anything beyond the default page is in effect. */
  isFiltered: boolean
}

export const PAGE_SIZES = [25, 50, 100, 200] as const

interface Options {
  defaultOrdering: string
  filterKeys: readonly string[]
  defaultPageSize?: number
}

/**
 * List state lives in the URL, not in component state.
 *
 * That makes a filtered view shareable with another operator, survivable
 * across a refresh, and navigable with the browser's back button — all things
 * an ops console gets asked for immediately.
 */
export function useListQueryState({
  defaultOrdering,
  filterKeys,
  defaultPageSize = 25,
}: Options): ListQueryApi {
  const [params, setParams] = useSearchParams()

  const state = useMemo<ListQueryState>(() => {
    const filters: Record<string, string> = {}
    for (const key of filterKeys) {
      const value = params.get(key)
      if (value !== null && value !== '') filters[key] = value
    }
    const page = Number(params.get('page') ?? '1')
    const pageSize = Number(params.get('page_size') ?? String(defaultPageSize))
    return {
      page: Number.isFinite(page) && page > 0 ? page : 1,
      pageSize: Number.isFinite(pageSize) && pageSize > 0 ? Math.min(200, pageSize) : defaultPageSize,
      search: params.get('search') ?? '',
      ordering: params.get('ordering') ?? defaultOrdering,
      filters,
    }
  }, [params, filterKeys, defaultOrdering, defaultPageSize])

  const update = useCallback(
    (mutate: (next: URLSearchParams) => void, resetPage = true) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          mutate(next)
          if (resetPage) next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  const setParam = useCallback(
    (key: string, value: string, fallback: string, resetPage = true) => {
      update((next) => {
        if (value === '' || value === fallback) next.delete(key)
        else next.set(key, value)
      }, resetPage)
    },
    [update],
  )

  return {
    ...state,
    isFiltered:
      state.search !== '' || Object.keys(state.filters).length > 0 || state.ordering !== defaultOrdering,
    setSearch: useCallback((value) => setParam('search', value, ''), [setParam]),
    setOrdering: useCallback(
      (value) => setParam('ordering', value, defaultOrdering),
      [setParam, defaultOrdering],
    ),
    setPage: useCallback(
      (value) =>
        update((next) => {
          if (value <= 1) next.delete('page')
          else next.set('page', String(value))
        }, false),
      [update],
    ),
    setPageSize: useCallback(
      (value) => setParam('page_size', String(value), String(defaultPageSize)),
      [setParam, defaultPageSize],
    ),
    setFilter: useCallback((key, value) => setParam(key, value, ''), [setParam]),
    clearFilters: useCallback(
      () =>
        update((next) => {
          next.delete('search')
          next.delete('ordering')
          for (const key of filterKeys) next.delete(key)
        }),
      [update, filterKeys],
    ),
  }
}
