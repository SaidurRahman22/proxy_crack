import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, History, Upload } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api, type ListParams } from '@/api/client'
import type { UploadBatch } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { Overlay } from '@/components/ui/Overlay'
import { Banner, EmptyState } from '@/components/ui/States'
import { useListQueryState } from '@/hooks/useListQueryState'
import { formatDateTime, formatNumber, formatRelative, pluralise, share } from '@/lib/format'

import { RowErrorReport } from './CsvUploadPage'

const NO_FILTERS = [] as const

export function UploadBatchesPage() {
  const list = useListQueryState({ defaultOrdering: '-created_at', filterKeys: NO_FILTERS })
  const [inspecting, setInspecting] = useState<UploadBatch | null>(null)

  const params: ListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
  }

  const query = useQuery({
    queryKey: ['upload-batches', params],
    queryFn: ({ signal }) => api.uploadBatches.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const columns: Array<Column<UploadBatch>> = [
    {
      key: 'id',
      header: 'Batch',
      tight: true,
      render: (batch) => (
        <button type="button" className="cell-link mono" onClick={() => setInspecting(batch)}>
          #{batch.id}
        </button>
      ),
    },
    {
      key: 'filename',
      header: 'File',
      sortKey: 'filename',
      render: (batch) => <span className="mono truncate">{batch.filename}</span>,
    },
    {
      key: 'uploaded_by',
      header: 'Uploaded by',
      tight: true,
      render: (batch) => <span className="truncate">{batch.uploaded_by}</span>,
    },
    {
      key: 'row_count',
      header: 'Rows',
      align: 'right',
      tight: true,
      render: (batch) => <span className="num">{formatNumber(batch.row_count)}</span>,
    },
    {
      key: 'created_count',
      header: 'Created',
      align: 'right',
      tight: true,
      render: (batch) => <span className="num">{formatNumber(batch.created_count)}</span>,
    },
    {
      key: 'updated_count',
      header: 'Updated',
      align: 'right',
      tight: true,
      render: (batch) => <span className="num">{formatNumber(batch.updated_count)}</span>,
    },
    {
      key: 'fail_count',
      header: 'Failed',
      align: 'right',
      tight: true,
      render: (batch) =>
        batch.fail_count === 0 ? (
          <span className="num muted">0</span>
        ) : (
          <button type="button" className="cell-link num danger-text" onClick={() => setInspecting(batch)}>
            {formatNumber(batch.fail_count)}
          </button>
        ),
    },
    {
      key: 'outcome',
      header: 'Outcome',
      tight: true,
      render: (batch) =>
        batch.fail_count === 0 ? (
          <span className="status status--alive">
            <CheckCircle2 size={13} className="status__icon" aria-hidden="true" />
            Clean
          </span>
        ) : (
          <span className="status status--warning">
            <AlertTriangle size={13} className="status__icon" aria-hidden="true" />
            {Math.round(share(batch.fail_count, batch.row_count) * 100)}% rejected
          </span>
        ),
    },
    {
      key: 'created_at',
      header: 'Imported',
      sortKey: 'created_at',
      tight: true,
      render: (batch) => (
        <time dateTime={batch.created_at} title={batch.created_at}>
          {formatRelative(batch.created_at)}
        </time>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Upload batches"
        description="Every CSV import, with the per-line reasons each rejected row was refused. Read-only."
        actions={
          <Link className="btn btn--primary" to="/upload">
            <Upload size={15} aria-hidden="true" />
            New import
          </Link>
        }
      />

      <section className="card">
        <Toolbar actions={<ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />}>
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search filename or uploader"
            busy={query.isFetching}
          />
        </Toolbar>

        <DataTable
          caption="CSV upload batches"
          columns={columns}
          rows={query.data?.results ?? []}
          rowKey={(batch) => batch.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          empty={
            <EmptyState
              icon={<History size={20} />}
              title={list.isFiltered ? 'No batches match that search' : 'Nothing imported yet'}
              body={
                list.isFiltered
                  ? 'Try a different filename.'
                  : 'Import a provider CSV and the batch will be recorded here.'
              }
              action={
                list.isFiltered ? (
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear search
                  </Button>
                ) : (
                  <Link className="btn btn--primary" to="/upload">
                    <Upload size={15} aria-hidden="true" />
                    Import a CSV
                  </Link>
                )
              }
            />
          }
        />

        <Pagination
          page={list.page}
          pageSize={list.pageSize}
          count={query.data?.count ?? 0}
          onPageChange={list.setPage}
          onPageSizeChange={list.setPageSize}
          label="batch"
          labelPlural="batches"
        />
      </section>

      <Overlay
        kind="drawer"
        wide
        open={inspecting !== null}
        onClose={() => setInspecting(null)}
        title={inspecting ? `Batch #${inspecting.id}` : ''}
        subtitle={inspecting?.filename}
      >
        {inspecting && (
          <>
            <div className="result-grid">
              <div className="result-tile">
                <div className="result-tile__value">{formatNumber(inspecting.row_count)}</div>
                <div className="result-tile__label">Rows read</div>
              </div>
              <div className="result-tile result-tile--ok">
                <div className="result-tile__value">{formatNumber(inspecting.created_count)}</div>
                <div className="result-tile__label">Created</div>
              </div>
              <div className="result-tile">
                <div className="result-tile__value">{formatNumber(inspecting.updated_count)}</div>
                <div className="result-tile__label">Updated</div>
              </div>
              <div
                className={
                  inspecting.fail_count > 0 ? 'result-tile result-tile--danger' : 'result-tile'
                }
              >
                <div className="result-tile__value">{formatNumber(inspecting.fail_count)}</div>
                <div className="result-tile__label">Failed</div>
              </div>
            </div>

            <dl className="kv">
              <dt>Uploaded by</dt>
              <dd>{inspecting.uploaded_by}</dd>
              <dt>Imported</dt>
              <dd>{formatDateTime(inspecting.created_at)}</dd>
              <dt>Succeeded</dt>
              <dd>
                {formatNumber(inspecting.success_count)}{' '}
                {pluralise(inspecting.success_count, 'row')}
              </dd>
            </dl>

            {inspecting.errors.length > 0 ? (
              <RowErrorReport errors={inspecting.errors} />
            ) : (
              <Banner tone="accent" icon={<CheckCircle2 size={15} aria-hidden="true" />}>
                Every row in this file was accepted.
              </Banner>
            )}
          </>
        )}
      </Overlay>
    </>
  )
}
