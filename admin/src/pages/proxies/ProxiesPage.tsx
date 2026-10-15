import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Network, Pencil, Plus, Power, PowerOff, Trash2, Upload, X } from 'lucide-react'
import { useCallback, useId, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, type ProxyListParams } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Protocol, ProxyListItem, ProxyTier } from '@/api/types'
import { ERROR_CODES, PROTOCOLS, PROXY_TIERS } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, FilterSelect, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { useConfirm } from '@/components/ui/ConfirmProvider'
import { AliveStatus, Badge, EnabledStatus, ProtocolChip, TierBadge } from '@/components/ui/Indicators'
import { EmptyState } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { useListQueryState } from '@/hooks/useListQueryState'
import { EM_DASH, formatLatency, formatNumber, formatRelative, pluralise } from '@/lib/format'

import { ProxyDetailDrawer } from './ProxyDetailDrawer'
import { ProxyFormDrawer } from './ProxyFormDrawer'

const FILTER_KEYS = ['protocol', 'tier', 'country', 'is_alive', 'is_enabled'] as const

const PROTOCOL_OPTIONS = PROTOCOLS.map((protocol) => ({ value: protocol, label: protocol }))
const TIER_OPTIONS = PROXY_TIERS.map((tier) => ({
  value: tier,
  label: tier[0]!.toUpperCase() + tier.slice(1),
}))

export function ProxiesPage() {
  const list = useListQueryState({
    defaultOrdering: 'host',
    filterKeys: FILTER_KEYS,
    defaultPageSize: 50,
  })
  const confirm = useConfirm()
  const toast = useToast()
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [detailFor, setDetailFor] = useState<ProxyListItem | null>(null)
  const [editing, setEditing] = useState<ProxyListItem | null>(null)
  const [creating, setCreating] = useState(false)

  const params: ProxyListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
    ...(list.filters.protocol ? { protocol: list.filters.protocol as Protocol } : {}),
    ...(list.filters.tier ? { tier: list.filters.tier as ProxyTier } : {}),
    ...(list.filters.country ? { country: list.filters.country.toUpperCase() } : {}),
    ...(list.filters.is_alive ? { is_alive: list.filters.is_alive === 'true' } : {}),
    ...(list.filters.is_enabled ? { is_enabled: list.filters.is_enabled === 'true' } : {}),
  }

  const query = useQuery({
    queryKey: ['proxies', params],
    queryFn: ({ signal }) => api.proxies.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const rows = useMemo(() => query.data?.results ?? [], [query.data])

  // Suggestions only — the operator can type any code the backend knows.
  const countrySuggestions = useMemo(
    () => [...new Set(rows.map((proxy) => proxy.country))].sort(),
    [rows],
  )

  const toggle = useCallback((id: number) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const setMany = useCallback((ids: number[], shouldSelect: boolean) => {
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) {
        if (shouldSelect) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }, [])

  function afterBulk(): void {
    setSelected(new Set())
    void queryClient.invalidateQueries({ queryKey: ['proxies'] })
    void queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  const bulkEnable = useMutation({
    mutationFn: ({ ids, enabled }: { ids: number[]; enabled: boolean }) =>
      api.proxies.bulkEnable(ids, enabled),
    onSuccess: (result, variables) => {
      afterBulk()
      toast.success(
        variables.enabled ? 'Proxies enabled' : 'Proxies disabled',
        `${formatNumber(result.updated)} ${pluralise(result.updated, 'proxy', 'proxies')} changed.`,
      )
    },
    onError: (error) => reportBulkError('Bulk update failed', error),
  })

  const bulkDelete = useMutation({
    mutationFn: (ids: number[]) => api.proxies.bulkDelete(ids),
    onSuccess: (result) => {
      afterBulk()
      toast.success(
        'Proxies deleted',
        `${formatNumber(result.deleted)} ${pluralise(result.deleted, 'proxy', 'proxies')} removed.`,
      )
    },
    onError: (error) => {
      // `proxy_in_use` is all-or-nothing: the batch was refused and nothing
      // was deleted. Name the ids that blocked it so the operator can act,
      // and keep the selection so they can narrow it.
      if (error instanceof ApiError && error.is(ERROR_CODES.proxyInUse)) {
        const blocking = error.numbers('proxy_ids')
        toast.error(
          'Nothing was deleted',
          blocking.length > 0
            ? `${formatNumber(blocking.length)} ${pluralise(blocking.length, 'proxy', 'proxies')} ` +
              `are still in use (id ${blocking.slice(0, 8).join(', ')}` +
              `${blocking.length > 8 ? `, +${blocking.length - 8} more` : ''}). ` +
              'Deselect them and try again.'
            : error.message,
        )
        return
      }
      reportBulkError('Bulk delete failed', error)
    },
  })

  const removeOne = useMutation({
    mutationFn: (proxy: ProxyListItem) => api.proxies.remove(proxy.id),
    onSuccess: (_result, proxy) => {
      afterBulk()
      toast.success('Proxy deleted', `${proxy.host}:${proxy.port}`)
    },
    onError: (error) => reportBulkError('Could not delete the proxy', error),
  })

  /**
   * Bulk endpoints are throttled at 20/hour. A 429 is an operational fact an
   * operator needs stated plainly, not folded into a generic failure.
   */
  function reportBulkError(fallbackTitle: string, error: unknown): void {
    if (error instanceof ApiError && error.status === 429) {
      toast.error('Rate limit reached', error.message)
      return
    }
    toast.error(fallbackTitle, error instanceof Error ? error.message : String(error))
  }

  const selectedIds = [...selected]

  async function onBulkEnable(enabled: boolean): Promise<void> {
    const confirmed = await confirm({
      title: enabled ? 'Enable the selected proxies?' : 'Disable the selected proxies?',
      body: (
        <>
          {formatNumber(selectedIds.length)} {pluralise(selectedIds.length, 'proxy', 'proxies')} will
          be {enabled ? 'returned to' : 'withheld from'} the pool handed out to clients.
          {!enabled && ' Existing sessions are not torn down.'}
        </>
      ),
      confirmLabel: enabled ? 'Enable them' : 'Disable them',
      tone: enabled ? 'primary' : 'danger',
    })
    if (confirmed) bulkEnable.mutate({ ids: selectedIds, enabled })
  }

  async function onBulkDelete(): Promise<void> {
    const confirmed = await confirm({
      title: `Delete ${formatNumber(selectedIds.length)} ${pluralise(selectedIds.length, 'proxy', 'proxies')}?`,
      body: (
        <>
          This permanently removes the records and their stored credentials. Chains referencing
          them will lose those hops. This cannot be undone.
        </>
      ),
      confirmLabel: 'Delete permanently',
      // A destructive bulk op gets a typed confirmation, not just a click.
      requireText: selectedIds.length >= 10 ? 'delete' : undefined,
    })
    if (confirmed) bulkDelete.mutate(selectedIds)
  }

  async function onDeleteOne(proxy: ProxyListItem): Promise<void> {
    const confirmed = await confirm({
      title: 'Delete this proxy?',
      body: (
        <>
          <strong className="mono">
            {proxy.host}:{proxy.port}
          </strong>{' '}
          and its stored credentials will be removed. This cannot be undone.
        </>
      ),
      confirmLabel: 'Delete proxy',
    })
    if (confirmed) removeOne.mutate(proxy)
  }

  const columns: Array<Column<ProxyListItem>> = [
    {
      key: 'host',
      header: 'Host',
      sortKey: 'host',
      render: (proxy) => (
        <button type="button" className="cell-link mono" onClick={() => setDetailFor(proxy)}>
          {proxy.host}
          <span className="muted">:{proxy.port}</span>
        </button>
      ),
    },
    {
      key: 'protocol',
      header: 'Protocol',
      tight: true,
      render: (proxy) => <ProtocolChip protocol={proxy.protocol} />,
    },
    {
      key: 'country',
      header: 'Location',
      tight: true,
      render: (proxy) => (
        <span className="row">
          <span className="mono">{proxy.country}</span>
          {proxy.city && <span className="muted truncate">{proxy.city}</span>}
        </span>
      ),
    },
    { key: 'tier', header: 'Tier', tight: true, render: (proxy) => <TierBadge tier={proxy.tier} /> },
    {
      key: 'is_alive',
      header: 'Health',
      tight: true,
      render: (proxy) => <AliveStatus alive={proxy.is_alive} />,
    },
    {
      key: 'is_enabled',
      header: 'Availability',
      tight: true,
      render: (proxy) => <EnabledStatus enabled={proxy.is_enabled} />,
    },
    {
      key: 'latency_ms',
      header: 'Latency',
      sortKey: 'latency_ms',
      align: 'right',
      tight: true,
      render: (proxy) => <LatencyCell ms={proxy.latency_ms} />,
    },
    {
      key: 'last_checked',
      header: 'Checked',
      sortKey: 'last_checked',
      tight: true,
      render: (proxy) => (
        <span className="muted" title={proxy.last_checked ?? undefined}>
          {formatRelative(proxy.last_checked)}
        </span>
      ),
    },
    {
      key: 'credentials',
      header: 'Auth',
      tight: true,
      render: (proxy) =>
        proxy.requires_credentials ? (
          <Badge tone="neutral" icon={<KeyRound size={11} aria-hidden="true" />}>
            Required
          </Badge>
        ) : (
          <span className="muted">None</span>
        ),
    },
    {
      key: 'batch',
      header: 'Batch',
      tight: true,
      render: (proxy) =>
        proxy.upload_batch === null ? (
          <span className="muted">{EM_DASH}</span>
        ) : (
          <Link className="cell-link mono" to={`/upload-batches?search=${proxy.upload_batch}`}>
            #{proxy.upload_batch}
          </Link>
        ),
    },
    {
      key: 'actions',
      header: '',
      srHeader: 'Row actions',
      tight: true,
      render: (proxy) => (
        <div className="row">
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Pencil size={14} />}
            onClick={() => setEditing(proxy)}
          >
            Edit {proxy.host}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Trash2 size={14} />}
            className="danger-text"
            onClick={() => void onDeleteOne(proxy)}
          >
            Delete {proxy.host}
          </Button>
        </div>
      ),
    },
  ]

  const bulkBusy = bulkEnable.isPending || bulkDelete.isPending

  return (
    <>
      <PageHeader
        title="Proxies"
        description="The pool handed to clients. Credentials are decrypted on the detail panel only, one deliberate request at a time."
        actions={
          <>
            <Link className="btn btn--ghost" to="/upload">
              <Upload size={15} aria-hidden="true" />
              Import CSV
            </Link>
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
              New proxy
            </Button>
          </>
        }
      />

      <section className="card">
        <Toolbar
          actions={
            <>
              <FilterSelect
                label="Filter by protocol"
                allLabel="Any protocol"
                value={list.filters.protocol ?? ''}
                onChange={(value) => list.setFilter('protocol', value)}
                options={PROTOCOL_OPTIONS}
              />
              <FilterSelect
                label="Filter by tier"
                allLabel="Any tier"
                value={list.filters.tier ?? ''}
                onChange={(value) => list.setFilter('tier', value)}
                options={TIER_OPTIONS}
              />
              <CountryFilter
                value={list.filters.country ?? ''}
                suggestions={countrySuggestions}
                onChange={(value) => list.setFilter('country', value)}
              />
              <FilterSelect
                label="Filter by health"
                allLabel="Any health"
                value={list.filters.is_alive ?? ''}
                onChange={(value) => list.setFilter('is_alive', value)}
                options={[
                  { value: 'true', label: 'Alive' },
                  { value: 'false', label: 'Dead' },
                ]}
              />
              <FilterSelect
                label="Filter by availability"
                allLabel="Any availability"
                value={list.filters.is_enabled ?? ''}
                onChange={(value) => list.setFilter('is_enabled', value)}
                options={[
                  { value: 'true', label: 'Enabled' },
                  { value: 'false', label: 'Disabled' },
                ]}
              />
              <ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />
            </>
          }
        >
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search host, country or city"
            busy={query.isFetching}
          />
        </Toolbar>

        {selected.size > 0 && (
          <div className="bulkbar" role="region" aria-label="Bulk actions">
            <span className="bulkbar__count">
              {formatNumber(selected.size)} selected
            </span>
            <Button
              variant="ghost"
              size="sm"
              icon={<Power size={14} />}
              disabled={bulkBusy}
              onClick={() => void onBulkEnable(true)}
            >
              Enable
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={<PowerOff size={14} />}
              disabled={bulkBusy}
              onClick={() => void onBulkEnable(false)}
            >
              Disable
            </Button>
            <Button
              variant="danger-ghost"
              size="sm"
              icon={<Trash2 size={14} />}
              disabled={bulkBusy}
              onClick={() => void onBulkDelete()}
            >
              Delete
            </Button>
            <span className="spacer" />
            <Button variant="quiet" size="sm" icon={<X size={14} />} onClick={() => setSelected(new Set())}>
              Clear selection
            </Button>
          </div>
        )}

        <DataTable
          caption="Proxy pool"
          columns={columns}
          rows={rows}
          rowKey={(proxy) => proxy.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          selection={{ selected, toggle, setMany }}
          // Page sizes go to 200 here; windowing keeps the DOM at ~25 rows.
          virtualize={rows.length > 60}
          empty={
            <EmptyState
              icon={<Network size={20} />}
              title={list.isFiltered ? 'No proxies match these filters' : 'The pool is empty'}
              body={
                list.isFiltered
                  ? 'Try a wider filter, or clear them to see the whole pool.'
                  : 'Import a provider CSV to populate the pool, or add a single proxy by hand.'
              }
              action={
                list.isFiltered ? (
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear filters
                  </Button>
                ) : (
                  <Link className="btn btn--primary" to="/upload">
                    <Upload size={15} aria-hidden="true" />
                    Import CSV
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
          label="proxy"
          labelPlural="proxies"
        />
      </section>

      <ProxyDetailDrawer
        proxy={detailFor}
        onClose={() => setDetailFor(null)}
        onEdit={(proxy) => {
          setDetailFor(null)
          setEditing(proxy)
        }}
      />
      <ProxyFormDrawer
        open={creating || editing !== null}
        proxy={editing}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
      />
    </>
  )
}

/**
 * Latency reads as a number first; the colour only reinforces it. A
 * colour-blind operator loses nothing, because the figure is the signal.
 */
function LatencyCell({ ms }: { ms: number | null }) {
  if (ms === null) return <span className="muted">{EM_DASH}</span>
  const tone = ms < 150 ? 'var(--accent)' : ms < 450 ? 'var(--subtle-fg)' : 'var(--warning)'
  return (
    <span className="num" style={{ color: tone }}>
      {formatLatency(ms)}
    </span>
  )
}

/**
 * Free-text country code with suggestions drawn from the current page.
 *
 * A `<select>` would need the full distinct-country list, which the contract
 * does not expose; a text input with a datalist works against any backend data
 * and still offers the codes actually in view.
 */
function CountryFilter({
  value,
  suggestions,
  onChange,
}: {
  value: string
  suggestions: string[]
  onChange: (value: string) => void
}) {
  const id = useId()
  const listId = `${id}-countries`
  const [draft, setDraft] = useState(value)
  const [lastValue, setLastValue] = useState(value)
  if (value !== lastValue) {
    setLastValue(value)
    setDraft(value)
  }

  return (
    <>
      <label className="visually-hidden" htmlFor={id}>
        Filter by country code
      </label>
      <input
        id={id}
        className="input"
        style={{ width: '8.5rem', height: '2.25rem', fontSize: 'var(--text-sm)' }}
        placeholder="Country"
        maxLength={2}
        list={listId}
        autoComplete="off"
        value={draft}
        onChange={(event) => {
          const next = event.target.value.toUpperCase()
          setDraft(next)
          if (next === '' || next.length === 2) onChange(next)
        }}
      />
      <datalist id={listId}>
        {suggestions.map((country) => (
          <option key={country} value={country} />
        ))}
      </datalist>
    </>
  )
}
