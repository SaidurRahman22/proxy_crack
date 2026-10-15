import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AlertTriangle, ChevronRight, Waypoints } from 'lucide-react'

import { api, type ChainListParams } from '@/api/client'
import type { Chain, ChainHop } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { Badge, ProtocolChip } from '@/components/ui/Indicators'
import { EmptyState } from '@/components/ui/States'
import { useListQueryState } from '@/hooks/useListQueryState'
import { formatRelative, pluralise } from '@/lib/format'

const NO_FILTERS = [] as const

/**
 * An HTTP hop proxies TCP only, so a chain containing one cannot carry UDP —
 * no QUIC, WebRTC, VoIP or most games. That is product-visible, so the column
 * says it rather than leaving the operator to infer it from the protocols.
 */
function carriesUdp(hops: ChainHop[]): boolean {
  return !hops.some((hop) => hop.proxy.protocol === 'http')
}

export function ChainsPage() {
  const list = useListQueryState({ defaultOrdering: '-created_at', filterKeys: NO_FILTERS })

  const params: ChainListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
  }

  const query = useQuery({
    queryKey: ['chains', params],
    queryFn: ({ signal }) => api.chains.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const columns: Array<Column<Chain>> = [
    {
      key: 'name',
      header: 'Chain',
      sortKey: 'name',
      render: (chain) => <span className="cell--strong truncate">{chain.name}</span>,
    },
    {
      key: 'user',
      header: 'User',
      render: (chain) => <span className="truncate">{chain.user.email}</span>,
    },
    {
      key: 'hops',
      header: 'Route',
      render: (chain) => (
        <div className="hops">
          {[...chain.hops]
            .sort((a, b) => a.position - b.position)
            .map((hop, index) => (
              <span className="row" key={hop.proxy.id} style={{ gap: '0.35rem' }}>
                {index > 0 && (
                  <ChevronRight size={12} className="hop__arrow" aria-hidden="true" />
                )}
                <span className="hop">
                  <ProtocolChip protocol={hop.proxy.protocol} />
                  {hop.proxy.host}
                  <span className="muted">{hop.proxy.country}</span>
                </span>
              </span>
            ))}
        </div>
      ),
    },
    {
      key: 'length',
      header: 'Hops',
      align: 'right',
      tight: true,
      render: (chain) => <span className="num">{chain.length ?? chain.hops.length}</span>,
    },
    {
      key: 'udp',
      header: 'UDP',
      tight: true,
      render: (chain) =>
        carriesUdp(chain.hops) ? (
          <Badge tone="accent">Carries UDP</Badge>
        ) : (
          <Badge tone="warning" icon={<AlertTriangle size={11} aria-hidden="true" />}>
            TCP only
          </Badge>
        ),
    },
    {
      key: 'created_at',
      header: 'Created',
      sortKey: 'created_at',
      tight: true,
      render: (chain) => (
        <time dateTime={chain.created_at} title={chain.created_at}>
          {formatRelative(chain.created_at)}
        </time>
      ),
    },
  ]

  const rows = query.data?.results ?? []
  const tcpOnly = rows.filter((chain) => !carriesUdp(chain.hops)).length

  return (
    <>
      <PageHeader
        title="User chains"
        description="Routes users have built, in client order: hop 1 is the first proxy their traffic enters. Read-only."
      />

      <section className="card">
        <Toolbar actions={<ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />}>
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search chain name or user email"
            busy={query.isFetching}
          />
        </Toolbar>

        <DataTable
          caption="User proxy chains"
          columns={columns}
          rows={rows}
          rowKey={(chain) => chain.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          empty={
            <EmptyState
              icon={<Waypoints size={20} />}
              title={list.isFiltered ? 'No chains match that search' : 'No chains built yet'}
              body={
                list.isFiltered
                  ? 'Try a different name or email.'
                  : 'Chains appear here as users assemble routes in the app.'
              }
              action={
                list.isFiltered ? (
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear search
                  </Button>
                ) : undefined
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
          label="chain"
        />
      </section>

      {tcpOnly > 0 && (
        <p className="muted" style={{ fontSize: 'var(--text-xs)' }}>
          {tcpOnly} {pluralise(tcpOnly, 'chain')} on this page {tcpOnly === 1 ? 'contains' : 'contain'}{' '}
          an HTTP hop and cannot carry UDP.
        </p>
      )}
    </>
  )
}
