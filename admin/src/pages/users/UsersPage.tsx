import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Pencil, Plus, Trash2, UserX, Users as UsersIcon } from 'lucide-react'
import { useState } from 'react'

import { api, type UserListParams } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { User } from '@/api/types'
import { ERROR_CODES } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import {
  ClearFiltersButton,
  FilterSelect,
  Pagination,
  SearchBox,
  Toolbar,
} from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { useConfirm } from '@/components/ui/ConfirmProvider'
import { ActiveStatus, Badge, SubscriptionStatusTag } from '@/components/ui/Indicators'
import { EmptyState } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { useAuth } from '@/auth/AuthProvider'
import { useListQueryState } from '@/hooks/useListQueryState'
import { EM_DASH, formatRelative, fullName } from '@/lib/format'

import { SetPasswordDialog, UserFormDrawer } from './UserForm'

const FILTER_KEYS = ['is_active', 'is_staff'] as const
const BOOL_OPTIONS = [
  { value: 'true', label: 'Yes' },
  { value: 'false', label: 'No' },
]

export function UsersPage() {
  const list = useListQueryState({ defaultOrdering: '-date_joined', filterKeys: FILTER_KEYS })
  const confirm = useConfirm()
  const toast = useToast()
  const queryClient = useQueryClient()
  const { user: currentUser } = useAuth()

  const [editing, setEditing] = useState<User | null>(null)
  const [creating, setCreating] = useState(false)
  const [passwordFor, setPasswordFor] = useState<User | null>(null)

  const params: UserListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
    ...(list.filters.is_active ? { is_active: list.filters.is_active === 'true' } : {}),
    ...(list.filters.is_staff ? { is_staff: list.filters.is_staff === 'true' } : {}),
  }

  const query = useQuery({
    queryKey: ['users', params],
    queryFn: ({ signal }) => api.users.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const remove = useMutation({
    mutationFn: (user: User) => api.users.remove(user.id),
    onSuccess: (_result, user) => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      toast.success('User deleted', user.email)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.is(ERROR_CODES.cannotDeleteSelf)) {
        toast.error('You cannot delete your own account', error.message)
        return
      }
      if (error instanceof ApiError && error.is(ERROR_CODES.privilegeChangeForbidden)) {
        toast.error('Not permitted', error.message)
        return
      }
      toast.error('Could not delete the user', error.message)
    },
  })

  async function onDelete(user: User): Promise<void> {
    const confirmed = await confirm({
      title: 'Delete this user?',
      body: (
        <>
          <strong>{user.email}</strong> and their subscription history will be removed. Usage logs
          and chains referencing them may be deleted by the backend too. This cannot be undone.
        </>
      ),
      confirmLabel: 'Delete user',
    })
    if (confirmed) remove.mutate(user)
  }

  const columns: Array<Column<User>> = [
    {
      key: 'email',
      header: 'Email',
      sortKey: 'email',
      render: (user) => (
        <button type="button" className="cell-link" onClick={() => setEditing(user)}>
          {user.email}
        </button>
      ),
    },
    {
      key: 'name',
      header: 'Name',
      render: (user) =>
        user.first_name || user.last_name ? (
          <span className="truncate">{fullName(user)}</span>
        ) : (
          <span className="muted">{EM_DASH}</span>
        ),
    },
    {
      key: 'subscription',
      header: 'Subscription',
      render: (user) =>
        user.subscription ? (
          <span className="row">
            <Badge tone="outline">{user.subscription.plan_code}</Badge>
            <SubscriptionStatusTag status={user.subscription.status} />
          </span>
        ) : (
          <span className="muted">No subscription</span>
        ),
    },
    {
      key: 'role',
      header: 'Role',
      tight: true,
      render: (user) =>
        user.is_superuser ? (
          <Badge tone="accent">Superuser</Badge>
        ) : user.is_staff ? (
          <Badge tone="info">Staff</Badge>
        ) : (
          <span className="muted">User</span>
        ),
    },
    {
      key: 'is_active',
      header: 'Status',
      tight: true,
      render: (user) => <ActiveStatus active={user.is_active} />,
    },
    {
      key: 'date_joined',
      header: 'Joined',
      sortKey: 'date_joined',
      tight: true,
      render: (user) => (
        <time dateTime={user.date_joined} title={user.date_joined}>
          {formatRelative(user.date_joined)}
        </time>
      ),
    },
    {
      key: 'last_login',
      header: 'Last login',
      sortKey: 'last_login',
      tight: true,
      render: (user) =>
        user.last_login ? (
          <time dateTime={user.last_login} title={user.last_login}>
            {formatRelative(user.last_login)}
          </time>
        ) : (
          <span className="muted">Never</span>
        ),
    },
    {
      key: 'actions',
      header: '',
      srHeader: 'Row actions',
      tight: true,
      render: (user) => (
        <div className="row" onClick={(event) => event.stopPropagation()}>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Pencil size={14} />}
            onClick={() => setEditing(user)}
          >
            Edit {user.email}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<KeyRound size={14} />}
            onClick={() => setPasswordFor(user)}
          >
            Set password for {user.email}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Trash2 size={14} />}
            className="danger-text"
            disabled={user.id === currentUser?.id}
            title={user.id === currentUser?.id ? 'You cannot delete your own account' : undefined}
            onClick={() => void onDelete(user)}
          >
            Delete {user.email}
          </Button>
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Users"
        description="Every account on the platform, with its current subscription and admin role."
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
            New user
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
                options={BOOL_OPTIONS.map((option) => ({ ...option, label: `Active: ${option.label}` }))}
              />
              <FilterSelect
                label="Filter by staff"
                allLabel="Any role"
                value={list.filters.is_staff ?? ''}
                onChange={(value) => list.setFilter('is_staff', value)}
                options={BOOL_OPTIONS.map((option) => ({ ...option, label: `Staff: ${option.label}` }))}
              />
              <ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />
            </>
          }
        >
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search email or name"
            busy={query.isFetching}
          />
        </Toolbar>

        <DataTable
          caption="Users"
          columns={columns}
          rows={query.data?.results ?? []}
          rowKey={(user) => user.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          empty={
            list.isFiltered ? (
              <EmptyState
                icon={<UserX size={20} />}
                title="No users match these filters"
                body="Try a different search term, or clear the filters to see every account."
                action={
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={<UsersIcon size={20} />}
                title="No users yet"
                body="Accounts appear here as soon as someone signs up, or you can create one directly."
                action={
                  <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
                    New user
                  </Button>
                }
              />
            )
          }
        />

        <Pagination
          page={list.page}
          pageSize={list.pageSize}
          count={query.data?.count ?? 0}
          onPageChange={list.setPage}
          onPageSizeChange={list.setPageSize}
          label="user"
        />
      </section>

      <UserFormDrawer
        open={creating || editing !== null}
        user={editing}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
      />
      <SetPasswordDialog user={passwordFor} onClose={() => setPasswordFor(null)} />
    </>
  )
}
