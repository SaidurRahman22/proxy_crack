import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Activity, ArrowDownToLine, ArrowUpFromLine } from 'lucide-react'
import { useId } from 'react'

import { api, type UsageLogListParams } from '@/api/client'
import type { UsageLog } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/States'
import { useListQueryState } from '@/hooks/useListQueryState'
import { EM_DASH, formatBytes, formatDateTime, formatNumber } from '@/lib/format'

const FILTER_KEYS = ['session_start_after', 'session_start_before'] as const

function duration(log: UsageLog): string {
  if (!log.session_end) return EM_DASH
  const ms = new Date(log.session_end).getTime() - new Date(log.session_start).getTime()
  if (!Number.isFinite(ms) || ms < 0) return EM_DASH
  const minutes = Math.round(ms / 60000)
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

export function UsageLogsPage() {
  const list = useListQueryState({ defaultOrdering: '-session_start', filterKeys: FILTER_KEYS })

  const params: UsageLogListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
    ...(list.filters.session_start_after
      ? { session_start_after: new Date(list.filters.session_start_after).toISOString() }
      : {}),
    ...(list.filters.session_start_before
      ? { session_start_before: new Date(`${list.filters.session_start_before}T23:59:59`).toISOString() }
      : {}),
  }

  const query = useQuery({
    queryKey: ['usage-logs', params],
    queryFn: ({ signal }) => api.usageLogs.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const rows = query.data?.results ?? []
  const pageUp = rows.reduce((sum, log) => sum + log.bytes_up, 0)
  const pageDown = rows.reduce((sum, log) => sum + log.bytes_down, 0)

  const columns: Array<Column<UsageLog>> = [
    {
      key: 'user',
      header: 'User',
      render: (log) => <span className="truncate">{log.user.email}</span>,
    },
    {
      key: 'session_start',
      header: 'Started',
      sortKey: 'session_start',
      tight: true,
      render: (log) => (
        <time className="num" dateTime={log.session_start}>
          {formatDateTime(log.session_start)}
        </time>
      ),
    },
    {
      key: 'session_end',
      header: 'Ended',
      sortKey: 'session_end',
      tight: true,
      render: (log) =>
        log.session_end ? (
          <time className="num" dateTime={log.session_end}>
            {formatDateTime(log.session_end)}
          </time>
        ) : (
          <span className="status status--warning">In progress</span>
        ),
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      tight: true,
      render: (log) => <span className="num">{duration(log)}</span>,
    },
    {
      key: 'bytes_down',
      header: 'Down',
      align: 'right',
      tight: true,
      render: (log) => <span className="num">{formatBytes(log.bytes_down)}</span>,
    },
    {
      key: 'bytes_up',
      header: 'Up',
      align: 'right',
      tight: true,
      render: (log) => <span className="num">{formatBytes(log.bytes_up)}</span>,
    },
    {
      key: 'total',
      header: 'Total',
      align: 'right',
      tight: true,
      // The backend sends bytes_total; fall back to the sum when it is absent.
      render: (log) => (
        <span className="num cell--strong">
          {formatBytes(log.bytes_total ?? log.bytes_up + log.bytes_down)}
        </span>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Usage logs"
        description="Per-session byte counters, as recorded for billing. Read-only — destinations are never logged."
      />

      <section className="card">
        <Toolbar
          actions={
            <>
              <DateFilter
                label="From"
                value={list.filters.session_start_after ?? ''}
                onChange={(value) => list.setFilter('session_start_after', value)}
              />
              <DateFilter
                label="To"
                value={list.filters.session_start_before ?? ''}
                onChange={(value) => list.setFilter('session_start_before', value)}
              />
              <ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />
            </>
          }
        >
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search by user email"
            busy={query.isFetching}
          />
        </Toolbar>

        <DataTable
          caption="Usage logs"
          columns={columns}
          rows={rows}
          rowKey={(log) => log.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          virtualize={rows.length > 60}
          empty={
            <EmptyState
              icon={<Activity size={20} />}
              title={list.isFiltered ? 'No sessions in this range' : 'No sessions recorded'}
              body={
                list.isFiltered
                  ? 'Widen the date range, or clear the filters.'
                  : 'Sessions appear here once clients start connecting.'
              }
              action={
                list.isFiltered ? (
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear filters
                  </Button>
                ) : undefined
              }
            />
          }
        />

        <div className="pagination" style={{ borderTop: '1px solid var(--border)' }}>
          <span className="row" style={{ gap: 'var(--space-md)' }}>
            <span className="row" style={{ gap: '0.35em' }}>
              <ArrowDownToLine size={13} aria-hidden="true" />
              <span className="num">{formatBytes(pageDown)}</span>
            </span>
            <span className="row" style={{ gap: '0.35em' }}>
              <ArrowUpFromLine size={13} aria-hidden="true" />
              <span className="num">{formatBytes(pageUp)}</span>
            </span>
            <span className="muted">on this page</span>
          </span>
        </div>

        <Pagination
          page={list.page}
          pageSize={list.pageSize}
          count={query.data?.count ?? 0}
          onPageChange={list.setPage}
          onPageSizeChange={list.setPageSize}
          label="session"
        />
      </section>

      <p className="muted" style={{ fontSize: 'var(--text-xs)' }}>
        {formatNumber(query.data?.count ?? 0)} sessions match the current filters.
      </p>
    </>
  )
}

function DateFilter({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  const id = useId()
  return (
    <>
      <label className="visually-hidden" htmlFor={id}>
        {label} date
      </label>
      <input
        id={id}
        type="date"
        className="input"
        style={{ width: 'auto', height: '2.25rem', fontSize: 'var(--text-sm)' }}
        aria-label={`${label} date`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </>
  )
}
