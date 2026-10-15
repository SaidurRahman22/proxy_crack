import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Pencil, Plus, Receipt, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { api, type SubscriptionListParams } from '@/api/client'
import type {
  Subscription,
  SubscriptionPlatform,
  SubscriptionStatus,
  SubscriptionWrite,
  User,
} from '@/api/types'
import { SUBSCRIPTION_PLATFORMS, SUBSCRIPTION_STATUSES } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, FilterSelect, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { useConfirm } from '@/components/ui/ConfirmProvider'
import { SelectField, TextField } from '@/components/ui/Field'
import { Badge, SubscriptionStatusTag } from '@/components/ui/Indicators'
import { Overlay } from '@/components/ui/Overlay'
import { EmptyState, FormError } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { UserPicker } from '@/components/ui/UserPicker'
import { useForm } from '@/hooks/useForm'
import { useListQueryState } from '@/hooks/useListQueryState'
import { EM_DASH, formatDate, formatDateTime, formatRelative } from '@/lib/format'

const FILTER_KEYS = ['status', 'plan'] as const

const STATUS_OPTIONS = SUBSCRIPTION_STATUSES.map((status) => ({
  value: status,
  label: status[0]!.toUpperCase() + status.slice(1),
}))

const PLATFORM_OPTIONS = SUBSCRIPTION_PLATFORMS.map((platform) => ({
  value: platform,
  label: platform === 'play' ? 'Google Play' : platform[0]!.toUpperCase() + platform.slice(1),
}))

/** `datetime-local` wants `YYYY-MM-DDTHH:mm`; the API speaks ISO-8601 UTC. */
function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function fromLocalInput(value: string): string | null {
  if (value === '') return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}

export function SubscriptionsPage() {
  const list = useListQueryState({ defaultOrdering: '-started_at', filterKeys: FILTER_KEYS })
  const confirm = useConfirm()
  const toast = useToast()
  const queryClient = useQueryClient()

  const [editing, setEditing] = useState<Subscription | null>(null)
  const [creating, setCreating] = useState(false)

  const plans = useQuery({
    queryKey: ['plans', 'all'],
    queryFn: ({ signal }) => api.plans.list({ page_size: 200, ordering: 'sort_order' }, signal),
    staleTime: 120_000,
  })

  const params: SubscriptionListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
    ...(list.filters.status ? { status: list.filters.status as SubscriptionStatus } : {}),
    ...(list.filters.plan ? { plan: Number(list.filters.plan) } : {}),
  }

  const query = useQuery({
    queryKey: ['subscriptions', params],
    queryFn: ({ signal }) => api.subscriptions.list(params, signal),
    placeholderData: keepPreviousData,
  })

  function invalidate(): void {
    void queryClient.invalidateQueries({ queryKey: ['subscriptions'] })
    void queryClient.invalidateQueries({ queryKey: ['users'] })
    void queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  const cancel = useMutation({
    mutationFn: (subscription: Subscription) => api.subscriptions.cancel(subscription.id),
    onSuccess: (_result, subscription) => {
      invalidate()
      toast.success('Subscription cancelled', subscription.user.email)
    },
    onError: (error) => toast.error('Could not cancel the subscription', error.message),
  })

  const remove = useMutation({
    mutationFn: (subscription: Subscription) => api.subscriptions.remove(subscription.id),
    onSuccess: (_result, subscription) => {
      invalidate()
      toast.success('Subscription deleted', subscription.user.email)
    },
    onError: (error) => toast.error('Could not delete the subscription', error.message),
  })

  async function onCancel(subscription: Subscription): Promise<void> {
    const confirmed = await confirm({
      title: 'Cancel this subscription?',
      body: (
        <>
          <strong>{subscription.user.email}</strong> will lose {subscription.plan.name} entitlements
          at the next server-side check. The purchase record is kept.
        </>
      ),
      confirmLabel: 'Cancel subscription',
      cancelLabel: 'Keep it',
    })
    if (confirmed) cancel.mutate(subscription)
  }

  async function onDelete(subscription: Subscription): Promise<void> {
    const confirmed = await confirm({
      title: 'Delete this subscription record?',
      body: (
        <>
          The entire record for <strong>{subscription.user.email}</strong> is removed, including its
          payment reference. Cancelling is usually what you want instead.
        </>
      ),
      confirmLabel: 'Delete record',
    })
    if (confirmed) remove.mutate(subscription)
  }

  const columns: Array<Column<Subscription>> = [
    {
      key: 'user',
      header: 'User',
      sortKey: 'user__email',
      render: (subscription) => (
        <button type="button" className="cell-link" onClick={() => setEditing(subscription)}>
          {subscription.user.email}
        </button>
      ),
    },
    {
      key: 'plan',
      header: 'Plan',
      tight: true,
      render: (subscription) => (
        <span className="row">
          <Badge tone="outline">{subscription.plan.code}</Badge>
          <span className="truncate">{subscription.plan.name}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      tight: true,
      render: (subscription) => <SubscriptionStatusTag status={subscription.status} />,
    },
    {
      key: 'platform',
      header: 'Platform',
      tight: true,
      render: (subscription) => (
        <Badge tone={subscription.platform === 'manual' ? 'neutral' : 'info'}>
          {subscription.platform}
        </Badge>
      ),
    },
    {
      key: 'started_at',
      header: 'Started',
      sortKey: 'started_at',
      tight: true,
      render: (subscription) => (
        <time dateTime={subscription.started_at} title={subscription.started_at}>
          {formatDate(subscription.started_at)}
        </time>
      ),
    },
    {
      key: 'expires_at',
      header: 'Expires',
      sortKey: 'expires_at',
      tight: true,
      render: (subscription) =>
        subscription.expires_at ? (
          <time dateTime={subscription.expires_at} title={subscription.expires_at}>
            {formatRelative(subscription.expires_at)}
          </time>
        ) : (
          <span className="muted">Never</span>
        ),
    },
    {
      key: 'txn',
      header: 'Transaction',
      tight: true,
      render: (subscription) =>
        subscription.platform_txn_id ? (
          <span className="mono truncate" style={{ maxWidth: '12rem', display: 'inline-block' }}>
            {subscription.platform_txn_id}
          </span>
        ) : (
          <span className="muted">{EM_DASH}</span>
        ),
    },
    {
      key: 'verified',
      header: 'Verified',
      tight: true,
      render: (subscription) => (
        <span className="muted" title={subscription.last_verified_at ?? undefined}>
          {formatRelative(subscription.last_verified_at)}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      srHeader: 'Row actions',
      tight: true,
      render: (subscription) => (
        <div className="row">
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Pencil size={14} />}
            onClick={() => setEditing(subscription)}
          >
            Edit subscription for {subscription.user.email}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Ban size={14} />}
            disabled={subscription.status === 'cancelled'}
            onClick={() => void onCancel(subscription)}
          >
            Cancel subscription for {subscription.user.email}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Trash2 size={14} />}
            className="danger-text"
            onClick={() => void onDelete(subscription)}
          >
            Delete subscription for {subscription.user.email}
          </Button>
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Subscriptions"
        description="Purchases and entitlements. Cancelling keeps the record; deleting does not."
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
            New subscription
          </Button>
        }
      />

      <section className="card">
        <Toolbar
          actions={
            <>
              <FilterSelect
                label="Filter by status"
                allLabel="Any status"
                value={list.filters.status ?? ''}
                onChange={(value) => list.setFilter('status', value)}
                options={STATUS_OPTIONS}
              />
              <FilterSelect
                label="Filter by plan"
                allLabel="Any plan"
                value={list.filters.plan ?? ''}
                onChange={(value) => list.setFilter('plan', value)}
                options={(plans.data?.results ?? []).map((plan) => ({
                  value: String(plan.id),
                  label: plan.name,
                }))}
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
          caption="Subscriptions"
          columns={columns}
          rows={query.data?.results ?? []}
          rowKey={(subscription) => subscription.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          empty={
            <EmptyState
              icon={<Receipt size={20} />}
              title={list.isFiltered ? 'No subscriptions match these filters' : 'No subscriptions yet'}
              body={
                list.isFiltered
                  ? 'Clear the filters to see every subscription.'
                  : 'Purchases made in the app appear here automatically.'
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

        <Pagination
          page={list.page}
          pageSize={list.pageSize}
          count={query.data?.count ?? 0}
          onPageChange={list.setPage}
          onPageSizeChange={list.setPageSize}
          label="subscription"
        />
      </section>

      <SubscriptionFormDrawer
        open={creating || editing !== null}
        subscription={editing}
        planOptions={(plans.data?.results ?? []).map((plan) => ({
          value: String(plan.id),
          label: `${plan.name} (${plan.code})`,
        }))}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
      />
    </>
  )
}

interface SubscriptionFormValues extends Record<string, unknown> {
  plan: string
  started_at: string
  expires_at: string
  status: SubscriptionStatus
  platform: SubscriptionPlatform
  platform_txn_id: string
}

function toValues(subscription: Subscription | null): SubscriptionFormValues {
  return {
    plan: subscription ? String(subscription.plan.id) : '',
    started_at: toLocalInput(subscription?.started_at ?? new Date().toISOString()),
    expires_at: toLocalInput(subscription?.expires_at ?? null),
    status: subscription?.status ?? 'active',
    platform: subscription?.platform ?? 'manual',
    platform_txn_id: subscription?.platform_txn_id ?? '',
  }
}

function SubscriptionFormDrawer({
  open,
  subscription,
  planOptions,
  onClose,
}: {
  open: boolean
  subscription: Subscription | null
  planOptions: Array<{ value: string; label: string }>
  onClose: () => void
}) {
  const form = useForm<SubscriptionFormValues>(toValues(subscription))
  const [pickedUser, setPickedUser] = useState<User | null>(null)
  const [userError, setUserError] = useState<string | undefined>(undefined)
  const queryClient = useQueryClient()
  const toast = useToast()
  const isEdit = subscription !== null

  const seed = `${subscription?.id ?? 'new'}:${open}`
  const [lastSeed, setLastSeed] = useState(seed)
  if (seed !== lastSeed) {
    setLastSeed(seed)
    form.reset(toValues(subscription))
    setPickedUser(null)
    setUserError(undefined)
  }

  const mutation = useMutation({
    mutationFn: (values: SubscriptionFormValues) => {
      const payload: SubscriptionWrite = {
        // Reads are nested, writes are ids. The explicit `_id` form avoids any
        // ambiguity with the nested read fields of the same name.
        user_id: subscription?.user.id ?? pickedUser?.id ?? 0,
        plan_id: Number(values.plan),
        started_at: fromLocalInput(values.started_at) ?? new Date().toISOString(),
        expires_at: fromLocalInput(values.expires_at),
        status: values.status,
        platform: values.platform,
        platform_txn_id: values.platform_txn_id.trim() === '' ? null : values.platform_txn_id.trim(),
      }
      return isEdit ? api.subscriptions.update(subscription.id, payload) : api.subscriptions.create(payload)
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['subscriptions'] })
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      toast.success(isEdit ? 'Subscription updated' : 'Subscription created', saved.user.email)
      onClose()
    },
    onError: (error) => form.applyError(error),
  })

  function onSubmit(event: FormEvent): void {
    event.preventDefault()
    if (!isEdit && !pickedUser) {
      setUserError('Choose the account this subscription belongs to.')
      return
    }
    const valid = form.validate((values) => ({
      plan: values.plan === '' ? 'Choose a plan.' : undefined,
      started_at: values.started_at === '' ? 'A start date is required.' : undefined,
      expires_at:
        values.expires_at !== '' &&
        fromLocalInput(values.expires_at)! < fromLocalInput(values.started_at)!
          ? 'The expiry must be after the start date.'
          : undefined,
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
      title={isEdit ? 'Edit subscription' : 'New subscription'}
      subtitle={isEdit ? subscription.user.email : 'Grants an entitlement without a store purchase.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} loading={mutation.isPending}>
            {isEdit ? 'Save changes' : 'Create subscription'}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="stack">
        <FormError message={form.formError} />

        {isEdit ? (
          <div className="field">
            <span className="field__label">User</span>
            <div className="picker-selected">
              <span className="truncate">{subscription.user.email}</span>
              <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                Fixed after creation
              </span>
            </div>
          </div>
        ) : (
          <UserPicker
            label="User"
            required
            full
            selected={pickedUser}
            error={userError}
            onSelect={(user) => {
              setPickedUser(user)
              setUserError(undefined)
            }}
          />
        )}

        <div className="form-grid">
          <SelectField
            label="Plan"
            required
            full
            placeholder="Choose a plan…"
            options={planOptions}
            value={form.values.plan}
            error={form.errors.plan}
            onChange={(event) => form.set('plan', event.target.value)}
          />
          <TextField
            label="Started"
            type="datetime-local"
            required
            value={form.values.started_at}
            error={form.errors.started_at}
            onChange={(event) => form.set('started_at', event.target.value)}
          />
          <TextField
            label="Expires"
            type="datetime-local"
            hint="Leave blank for a perpetual entitlement."
            value={form.values.expires_at}
            error={form.errors.expires_at}
            onChange={(event) => form.set('expires_at', event.target.value)}
          />
          <SelectField
            label="Status"
            options={STATUS_OPTIONS}
            value={form.values.status}
            error={form.errors.status}
            onChange={(event) => form.set('status', event.target.value as SubscriptionStatus)}
          />
          <SelectField
            label="Platform"
            options={PLATFORM_OPTIONS}
            value={form.values.platform}
            error={form.errors.platform}
            onChange={(event) => form.set('platform', event.target.value as SubscriptionPlatform)}
          />
          <TextField
            label="Platform transaction ID"
            full
            hint="The store's purchase token. Blank for manual grants."
            value={form.values.platform_txn_id}
            error={form.errors.platform_txn_id}
            onChange={(event) => form.set('platform_txn_id', event.target.value)}
          />
        </div>

        {isEdit && subscription.last_verified_at && (
          <p className="muted" style={{ fontSize: 'var(--text-xs)' }}>
            Last verified against the store on {formatDateTime(subscription.last_verified_at)}.
          </p>
        )}

        <button type="submit" className="visually-hidden">
          Save
        </button>
      </form>
    </Overlay>
  )
}
