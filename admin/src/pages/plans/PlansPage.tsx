import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Layers, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { api, type PlanListParams } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Plan, PlanWrite, ProxyTier } from '@/api/types'
import { ERROR_CODES } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, FilterSelect, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { useConfirm } from '@/components/ui/ConfirmProvider'
import { SelectField, TextField, ToggleField } from '@/components/ui/Field'
import { ActiveStatus, Badge, TierBadge } from '@/components/ui/Indicators'
import { Overlay } from '@/components/ui/Overlay'
import { EmptyState, FormError } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { useForm } from '@/hooks/useForm'
import { useListQueryState } from '@/hooks/useListQueryState'
import { EM_DASH, formatBytes, formatNumber, formatPrice, pluralise } from '@/lib/format'

const FILTER_KEYS = ['is_active'] as const

const TIER_OPTIONS: Array<{ value: ProxyTier; label: string }> = [
  { value: 'free', label: 'Free' },
  { value: 'premium', label: 'Premium' },
]

export function PlansPage() {
  const list = useListQueryState({ defaultOrdering: 'sort_order', filterKeys: FILTER_KEYS })
  const confirm = useConfirm()
  const toast = useToast()
  const queryClient = useQueryClient()

  const [editing, setEditing] = useState<Plan | null>(null)
  const [creating, setCreating] = useState(false)

  const params: PlanListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
    ...(list.filters.is_active ? { is_active: list.filters.is_active === 'true' } : {}),
  }

  const query = useQuery({
    queryKey: ['plans', params],
    queryFn: ({ signal }) => api.plans.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const remove = useMutation({
    mutationFn: (plan: Plan) => api.plans.remove(plan.id),
    onSuccess: (_result, plan) => {
      void queryClient.invalidateQueries({ queryKey: ['plans'] })
      toast.success('Plan deleted', plan.code)
    },
    onError: (error) => {
      if (!(error instanceof ApiError)) {
        toast.error('Could not delete the plan', String(error))
        return
      }
      // The 409 guards carry their own explanation; `plan_has_active_
      // subscriptions` also carries the count, which is more useful as the
      // headline than a generic "conflict".
      if (error.is(ERROR_CODES.planHasActiveSubscriptions)) {
        const count = error.number('active_subscriptions')
        toast.error(
          count === null
            ? 'Plan still has active subscriptions'
            : `${formatNumber(count)} active ${pluralise(count, 'subscription')} on this plan`,
          error.message,
        )
      } else if (error.is(ERROR_CODES.lastFreePlan)) {
        toast.error('This is the last free-tier plan', error.message)
      } else if (error.is(ERROR_CODES.planInUse)) {
        toast.error('Plan is still in use', error.message)
      } else {
        toast.error('Could not delete the plan', error.message)
      }
    },
  })

  async function onDelete(plan: Plan): Promise<void> {
    const active = plan.active_subscription_count ?? 0
    const confirmed = await confirm({
      title: 'Delete this plan?',
      body: (
        <>
          <strong>{plan.name}</strong> ({plan.code}) will be removed.{' '}
          {active > 0 ? (
            <>
              It currently has <strong>{formatNumber(active)}</strong> active{' '}
              {pluralise(active, 'subscription')}, so the API will refuse this until they are
              migrated or cancelled.
            </>
          ) : (
            'The API refuses this if any subscription is still active on the plan.'
          )}
        </>
      ),
      confirmLabel: 'Delete plan',
    })
    if (confirmed) remove.mutate(plan)
  }

  const columns: Array<Column<Plan>> = [
    {
      key: 'code',
      header: 'Code',
      sortKey: 'code',
      render: (plan) => (
        <button type="button" className="cell-link mono" onClick={() => setEditing(plan)}>
          {plan.code}
        </button>
      ),
    },
    { key: 'name', header: 'Name', sortKey: 'name', render: (plan) => plan.name },
    {
      key: 'price',
      header: 'Price',
      sortKey: 'price',
      align: 'right',
      tight: true,
      render: (plan) => <span className="num">{formatPrice(plan.price, plan.currency)}</span>,
    },
    {
      key: 'active_subscription_count',
      header: 'Active subs',
      align: 'right',
      tight: true,
      render: (plan) =>
        plan.active_subscription_count === undefined ? (
          <span className="muted">{EM_DASH}</span>
        ) : (
          <span className="num">{formatNumber(plan.active_subscription_count)}</span>
        ),
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      tight: true,
      render: (plan) =>
        plan.duration_days === 0 ? (
          <span className="muted">Perpetual</span>
        ) : (
          <span className="num">
            {formatNumber(plan.duration_days)} {pluralise(plan.duration_days, 'day')}
          </span>
        ),
    },
    {
      key: 'chain',
      header: 'Hops',
      align: 'right',
      tight: true,
      render: (plan) => <span className="num">{plan.max_chain_length}</span>,
    },
    {
      key: 'tier',
      header: 'Max tier',
      tight: true,
      render: (plan) => <TierBadge tier={plan.max_proxy_tier} />,
    },
    {
      key: 'devices',
      header: 'Devices',
      align: 'right',
      tight: true,
      render: (plan) => <span className="num">{plan.max_devices}</span>,
    },
    {
      key: 'bandwidth',
      header: 'Bandwidth',
      align: 'right',
      tight: true,
      render: (plan) =>
        plan.bandwidth_limit_bytes === null ? (
          <span className="muted">Unmetered</span>
        ) : (
          <span className="num">{formatBytes(plan.bandwidth_limit_bytes, 0)}</span>
        ),
    },
    {
      key: 'flags',
      header: 'Flags',
      tight: true,
      render: (plan) => (
        <span className="row">
          {plan.is_free_tier && <Badge tone="outline">Free tier</Badge>}
          {plan.play_product_id && <Badge tone="info">Play</Badge>}
        </span>
      ),
    },
    {
      key: 'is_active',
      header: 'Status',
      tight: true,
      render: (plan) => <ActiveStatus active={plan.is_active} />,
    },
    {
      key: 'actions',
      header: '',
      srHeader: 'Row actions',
      tight: true,
      render: (plan) => (
        <div className="row">
          <Button variant="quiet" size="sm" iconOnly icon={<Pencil size={14} />} onClick={() => setEditing(plan)}>
            Edit {plan.code}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Trash2 size={14} />}
            className="danger-text"
            onClick={() => void onDelete(plan)}
          >
            Delete {plan.code}
          </Button>
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Plans"
        description="Entitlement definitions. Chain length and proxy tier here are enforced server-side on every request."
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
            New plan
          </Button>
        }
      />

      <section className="card">
        <Toolbar
          actions={
            <>
              <FilterSelect
                label="Filter by active"
                allLabel="Any status"
                value={list.filters.is_active ?? ''}
                onChange={(value) => list.setFilter('is_active', value)}
                options={[
                  { value: 'true', label: 'Active' },
                  { value: 'false', label: 'Inactive' },
                ]}
              />
              <ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />
            </>
          }
        >
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search code or name"
            busy={query.isFetching}
          />
        </Toolbar>

        <DataTable
          caption="Subscription plans"
          columns={columns}
          rows={query.data?.results ?? []}
          rowKey={(plan) => plan.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          empty={
            <EmptyState
              icon={<Layers size={20} />}
              title={list.isFiltered ? 'No plans match these filters' : 'No plans defined'}
              body={
                list.isFiltered
                  ? 'Clear the filters to see every plan.'
                  : 'Create at least one free-tier plan so new accounts have an entitlement.'
              }
              action={
                list.isFiltered ? (
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear filters
                  </Button>
                ) : (
                  <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
                    New plan
                  </Button>
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
          label="plan"
        />
      </section>

      <PlanFormDrawer
        open={creating || editing !== null}
        plan={editing}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
      />
    </>
  )
}

interface PlanFormValues extends Record<string, unknown> {
  code: string
  name: string
  price: string
  duration_days: string
  max_chain_length: string
  max_proxy_tier: ProxyTier
  max_devices: string
  bandwidth_limit_bytes: string
  is_free_tier: boolean
  is_active: boolean
  sort_order: string
  play_product_id: string
  description: string
}

function toValues(plan: Plan | null): PlanFormValues {
  return {
    code: plan?.code ?? '',
    name: plan?.name ?? '',
    price: plan?.price ?? '0.00',
    duration_days: String(plan?.duration_days ?? 30),
    max_chain_length: String(plan?.max_chain_length ?? 1),
    max_proxy_tier: plan?.max_proxy_tier ?? 'free',
    max_devices: String(plan?.max_devices ?? 1),
    bandwidth_limit_bytes:
      plan?.bandwidth_limit_bytes === null || plan?.bandwidth_limit_bytes === undefined
        ? ''
        : String(plan.bandwidth_limit_bytes),
    is_free_tier: plan?.is_free_tier ?? false,
    is_active: plan?.is_active ?? true,
    sort_order: String(plan?.sort_order ?? 0),
    play_product_id: plan?.play_product_id ?? '',
    description: plan?.description ?? '',
  }
}

function PlanFormDrawer({
  open,
  plan,
  onClose,
}: {
  open: boolean
  plan: Plan | null
  onClose: () => void
}) {
  const form = useForm<PlanFormValues>(toValues(plan))
  const queryClient = useQueryClient()
  const toast = useToast()
  const isEdit = plan !== null

  const seed = `${plan?.id ?? 'new'}:${open}`
  const [lastSeed, setLastSeed] = useState(seed)
  if (seed !== lastSeed) {
    setLastSeed(seed)
    form.reset(toValues(plan))
  }

  const mutation = useMutation({
    mutationFn: (values: PlanFormValues) => {
      const payload: PlanWrite = {
        code: values.code.trim(),
        name: values.name.trim(),
        price: values.price.trim(),
        ...(plan?.currency ? { currency: plan.currency } : {}),
        ...(values.description.trim() === '' ? {} : { description: values.description.trim() }),
        duration_days: Number(values.duration_days),
        max_chain_length: Number(values.max_chain_length),
        max_proxy_tier: values.max_proxy_tier,
        max_devices: Number(values.max_devices),
        bandwidth_limit_bytes:
          values.bandwidth_limit_bytes.trim() === '' ? null : Number(values.bandwidth_limit_bytes),
        is_free_tier: values.is_free_tier,
        is_active: values.is_active,
        sort_order: Number(values.sort_order),
        play_product_id: values.play_product_id.trim() === '' ? null : values.play_product_id.trim(),
      }
      return isEdit ? api.plans.update(plan.id, payload) : api.plans.create(payload)
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['plans'] })
      toast.success(isEdit ? 'Plan updated' : 'Plan created', saved.code)
      onClose()
    },
    onError: (error) => form.applyError(error),
  })

  function onSubmit(event: FormEvent): void {
    event.preventDefault()
    const valid = form.validate((values) => ({
      code: values.code.trim() === '' ? 'A code is required.' : undefined,
      name: values.name.trim() === '' ? 'A name is required.' : undefined,
      price: Number.isFinite(Number(values.price)) ? undefined : 'Enter a number, e.g. 9.99.',
      duration_days:
        Number.isInteger(Number(values.duration_days)) && Number(values.duration_days) >= 0
          ? undefined
          : 'Enter a whole number of days (0 for perpetual).',
      max_chain_length:
        Number(values.max_chain_length) >= 1
          ? undefined
          : 'A chain needs at least one hop.',
      max_devices: Number(values.max_devices) >= 1 ? undefined : 'At least one device.',
      bandwidth_limit_bytes:
        values.bandwidth_limit_bytes.trim() === '' || Number(values.bandwidth_limit_bytes) > 0
          ? undefined
          : 'Leave blank for unmetered, or enter a byte count.',
    }))
    if (!valid) return
    mutation.mutate(form.values)
  }

  return (
    <Overlay
      kind="drawer"
      open={open}
      onClose={onClose}
      busy={mutation.isPending}
      title={isEdit ? 'Edit plan' : 'New plan'}
      subtitle={isEdit ? plan.name : 'Defines what a subscriber is entitled to.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} loading={mutation.isPending}>
            {isEdit ? 'Save changes' : 'Create plan'}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="stack">
        <FormError message={form.formError} />

        <div className="form-grid">
          <TextField
            label="Code"
            required
            hint="Stable identifier, e.g. pro_m."
            value={form.values.code}
            error={form.errors.code}
            onChange={(event) => form.set('code', event.target.value)}
          />
          <TextField
            label="Name"
            required
            value={form.values.name}
            error={form.errors.name}
            onChange={(event) => form.set('name', event.target.value)}
          />
          <TextField
            label="Price"
            inputMode="decimal"
            value={form.values.price}
            error={form.errors.price}
            onChange={(event) => form.set('price', event.target.value)}
          />
          <TextField
            label="Duration (days)"
            inputMode="numeric"
            hint="0 means it never expires."
            value={form.values.duration_days}
            error={form.errors.duration_days}
            onChange={(event) => form.set('duration_days', event.target.value)}
          />
          <TextField
            label="Max chain length"
            inputMode="numeric"
            hint="Each hop adds a full round trip."
            value={form.values.max_chain_length}
            error={form.errors.max_chain_length}
            onChange={(event) => form.set('max_chain_length', event.target.value)}
          />
          <SelectField
            label="Max proxy tier"
            options={TIER_OPTIONS}
            value={form.values.max_proxy_tier}
            error={form.errors.max_proxy_tier}
            onChange={(event) => form.set('max_proxy_tier', event.target.value as ProxyTier)}
          />
          <TextField
            label="Max devices"
            inputMode="numeric"
            value={form.values.max_devices}
            error={form.errors.max_devices}
            onChange={(event) => form.set('max_devices', event.target.value)}
          />
          <TextField
            label="Sort order"
            inputMode="numeric"
            value={form.values.sort_order}
            error={form.errors.sort_order}
            onChange={(event) => form.set('sort_order', event.target.value)}
          />
          <TextField
            label="Bandwidth limit (bytes)"
            inputMode="numeric"
            full
            hint={
              form.values.bandwidth_limit_bytes.trim() === ''
                ? 'Blank means unmetered.'
                : `= ${formatBytes(Number(form.values.bandwidth_limit_bytes), 1)}`
            }
            value={form.values.bandwidth_limit_bytes}
            error={form.errors.bandwidth_limit_bytes}
            onChange={(event) => form.set('bandwidth_limit_bytes', event.target.value)}
          />
          <TextField
            label="Description"
            full
            hint="Shown on the paywall in the app."
            value={form.values.description}
            error={form.errors.description}
            onChange={(event) => form.set('description', event.target.value)}
          />
          <TextField
            label="Play product ID"
            full
            hint="Google Play Billing SKU. Blank for plans not sold on Android."
            value={form.values.play_product_id}
            error={form.errors.play_product_id}
            onChange={(event) => form.set('play_product_id', event.target.value)}
          />
        </div>

        <ToggleField
          label="Free tier"
          hint="The plan assigned to accounts with no purchase."
          checked={form.values.is_free_tier}
          onChange={(checked) => form.set('is_free_tier', checked)}
        />
        <ToggleField
          label="Active"
          hint="Inactive plans stay attached to existing subscriptions but cannot be bought."
          checked={form.values.is_active}
          onChange={(checked) => form.set('is_active', checked)}
        />

        <p className="muted" style={{ fontSize: 'var(--text-xs)' }}>
          {EM_DASH} Deleting a plan with active subscriptions is refused by the API.
        </p>

        <button type="submit" className="visually-hidden">
          Save
        </button>
      </form>
    </Overlay>
  )
}
