import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { api, type ContentListParams } from '@/api/client'
import type { ContentItem, ContentWrite } from '@/api/types'
import { CONTENT_KINDS } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { DataTable, type Column } from '@/components/table/DataTable'
import { ClearFiltersButton, FilterSelect, Pagination, SearchBox, Toolbar } from '@/components/table/Toolbar'
import { Button } from '@/components/ui/Button'
import { useConfirm } from '@/components/ui/ConfirmProvider'
import { TextAreaField, TextField, ToggleField } from '@/components/ui/Field'
import { Badge, PublishedStatus } from '@/components/ui/Indicators'
import { Overlay } from '@/components/ui/Overlay'
import { EmptyState, FormError } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { useForm } from '@/hooks/useForm'
import { useListQueryState } from '@/hooks/useListQueryState'
import { formatRelative } from '@/lib/format'

const FILTER_KEYS = ['kind', 'is_published'] as const

const KIND_OPTIONS = CONTENT_KINDS.map((kind) => ({
  value: kind,
  label: kind[0]!.toUpperCase() + kind.slice(1),
}))

export function ContentPage() {
  const list = useListQueryState({ defaultOrdering: '-priority', filterKeys: FILTER_KEYS })
  const confirm = useConfirm()
  const toast = useToast()
  const queryClient = useQueryClient()

  const [editing, setEditing] = useState<ContentItem | null>(null)
  const [creating, setCreating] = useState(false)

  const params: ContentListParams = {
    page: list.page,
    page_size: list.pageSize,
    ordering: list.ordering,
    ...(list.search ? { search: list.search } : {}),
    ...(list.filters.kind ? { kind: list.filters.kind } : {}),
    ...(list.filters.is_published ? { is_published: list.filters.is_published === 'true' } : {}),
  }

  const query = useQuery({
    queryKey: ['content', params],
    queryFn: ({ signal }) => api.content.list(params, signal),
    placeholderData: keepPreviousData,
  })

  const remove = useMutation({
    mutationFn: (item: ContentItem) => api.content.remove(item.id),
    onSuccess: (_result, item) => {
      void queryClient.invalidateQueries({ queryKey: ['content'] })
      toast.success('Content deleted', item.key)
    },
    onError: (error) => toast.error('Could not delete the item', error.message),
  })

  async function onDelete(item: ContentItem): Promise<void> {
    const confirmed = await confirm({
      title: 'Delete this content item?',
      body: (
        <>
          <strong className="mono">{item.key}</strong> will disappear from the app immediately. If
          you only want to hide it, unpublish it instead.
        </>
      ),
      confirmLabel: 'Delete item',
    })
    if (confirmed) remove.mutate(item)
  }

  const columns: Array<Column<ContentItem>> = [
    {
      key: 'key',
      header: 'Key',
      sortKey: 'key',
      render: (item) => (
        <button type="button" className="cell-link mono" onClick={() => setEditing(item)}>
          {item.key}
        </button>
      ),
    },
    { key: 'title', header: 'Title', render: (item) => <span className="truncate">{item.title}</span> },
    {
      key: 'kind',
      header: 'Kind',
      tight: true,
      render: (item) => <Badge tone="outline">{item.kind}</Badge>,
    },
    {
      key: 'body',
      header: 'Body',
      render: (item) => (
        <span className="muted truncate" style={{ display: 'block', maxWidth: '28rem' }}>
          {item.body}
        </span>
      ),
    },
    {
      key: 'priority',
      header: 'Priority',
      sortKey: 'priority',
      align: 'right',
      tight: true,
      render: (item) => <span className="num">{item.priority}</span>,
    },
    {
      key: 'is_published',
      header: 'Status',
      tight: true,
      render: (item) => <PublishedStatus published={item.is_published} />,
    },
    {
      key: 'updated_at',
      header: 'Updated',
      sortKey: 'updated_at',
      tight: true,
      render: (item) => (
        <time dateTime={item.updated_at} title={item.updated_at}>
          {formatRelative(item.updated_at)}
        </time>
      ),
    },
    {
      key: 'actions',
      header: '',
      srHeader: 'Row actions',
      tight: true,
      render: (item) => (
        <div className="row">
          <Button variant="quiet" size="sm" iconOnly icon={<Pencil size={14} />} onClick={() => setEditing(item)}>
            Edit {item.key}
          </Button>
          <Button
            variant="quiet"
            size="sm"
            iconOnly
            icon={<Trash2 size={14} />}
            className="danger-text"
            onClick={() => void onDelete(item)}
          >
            Delete {item.key}
          </Button>
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Content"
        description="Announcements, FAQ entries, tips and legal text served to the app by key."
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
            New item
          </Button>
        }
      />

      <section className="card">
        <Toolbar
          actions={
            <>
              <FilterSelect
                label="Filter by kind"
                allLabel="Any kind"
                value={list.filters.kind ?? ''}
                onChange={(value) => list.setFilter('kind', value)}
                options={KIND_OPTIONS}
              />
              <FilterSelect
                label="Filter by published"
                allLabel="Any status"
                value={list.filters.is_published ?? ''}
                onChange={(value) => list.setFilter('is_published', value)}
                options={[
                  { value: 'true', label: 'Published' },
                  { value: 'false', label: 'Draft' },
                ]}
              />
              <ClearFiltersButton show={list.isFiltered} onClear={list.clearFilters} />
            </>
          }
        >
          <SearchBox
            value={list.search}
            onChange={list.setSearch}
            placeholder="Search key, title or body"
            busy={query.isFetching}
          />
        </Toolbar>

        <DataTable
          caption="Content items"
          columns={columns}
          rows={query.data?.results ?? []}
          rowKey={(item) => item.id}
          isLoading={query.isPending}
          isFetching={query.isFetching}
          error={query.isError ? query.error : undefined}
          onRetry={() => void query.refetch()}
          ordering={list.ordering}
          onOrderingChange={list.setOrdering}
          totalCount={query.data?.count}
          empty={
            <EmptyState
              icon={<FileText size={20} />}
              title={list.isFiltered ? 'No content matches these filters' : 'No content yet'}
              body={
                list.isFiltered
                  ? 'Clear the filters to see every item.'
                  : 'Add the welcome banner, FAQ entries and legal text the app expects.'
              }
              action={
                list.isFiltered ? (
                  <Button variant="secondary" onClick={list.clearFilters}>
                    Clear filters
                  </Button>
                ) : (
                  <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
                    New item
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
          label="item"
        />
      </section>

      <ContentFormDrawer
        open={creating || editing !== null}
        item={editing}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
      />
    </>
  )
}

interface ContentFormValues extends Record<string, unknown> {
  key: string
  kind: string
  title: string
  body: string
  priority: string
  is_published: boolean
}

function toValues(item: ContentItem | null): ContentFormValues {
  return {
    key: item?.key ?? '',
    kind: item?.kind ?? 'announcement',
    title: item?.title ?? '',
    body: item?.body ?? '',
    priority: String(item?.priority ?? 0),
    is_published: item?.is_published ?? false,
  }
}

function ContentFormDrawer({
  open,
  item,
  onClose,
}: {
  open: boolean
  item: ContentItem | null
  onClose: () => void
}) {
  const form = useForm<ContentFormValues>(toValues(item))
  const queryClient = useQueryClient()
  const toast = useToast()
  const isEdit = item !== null

  const seed = `${item?.id ?? 'new'}:${open}`
  const [lastSeed, setLastSeed] = useState(seed)
  if (seed !== lastSeed) {
    setLastSeed(seed)
    form.reset(toValues(item))
  }

  const mutation = useMutation({
    mutationFn: (values: ContentFormValues) => {
      const payload: ContentWrite = {
        key: values.key.trim(),
        kind: values.kind.trim(),
        title: values.title.trim(),
        body: values.body,
        priority: Number(values.priority),
        is_published: values.is_published,
      }
      return isEdit ? api.content.update(item.id, payload) : api.content.create(payload)
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['content'] })
      toast.success(isEdit ? 'Content updated' : 'Content created', saved.key)
      onClose()
    },
    onError: (error) => form.applyError(error),
  })

  function onSubmit(event: FormEvent): void {
    event.preventDefault()
    const valid = form.validate((values) => ({
      key:
        values.key.trim() === ''
          ? 'A key is required — the app looks items up by it.'
          : /^[a-z0-9_]+$/.test(values.key.trim())
            ? undefined
            : 'Use lowercase letters, digits and underscores only.',
      title: values.title.trim() === '' ? 'A title is required.' : undefined,
      priority: Number.isInteger(Number(values.priority)) ? undefined : 'Enter a whole number.',
    }))
    if (!valid) return
    mutation.mutate(form.values)
  }

  return (
    <Overlay
      kind="drawer"
      wide
      open={open}
      onClose={onClose}
      busy={mutation.isPending}
      title={isEdit ? 'Edit content' : 'New content item'}
      subtitle={isEdit ? item.key : 'Served to the app by key.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} loading={mutation.isPending}>
            {isEdit ? 'Save changes' : 'Create item'}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="stack">
        <FormError message={form.formError} />

        <div className="form-grid">
          <TextField
            label="Key"
            required
            hint="Stable lookup key, e.g. welcome_banner."
            value={form.values.key}
            error={form.errors.key}
            onChange={(event) => form.set('key', event.target.value)}
          />
          <TextField
            label="Kind"
            required
            list="content-kinds"
            hint="Pick one of the known kinds or type your own."
            value={form.values.kind}
            error={form.errors.kind}
            onChange={(event) => form.set('kind', event.target.value)}
          />
          <TextField
            label="Title"
            required
            full
            value={form.values.title}
            error={form.errors.title}
            onChange={(event) => form.set('title', event.target.value)}
          />
          <TextAreaField
            label="Body"
            full
            rows={9}
            value={form.values.body}
            error={form.errors.body}
            onChange={(event) => form.set('body', event.target.value)}
          />
          <TextField
            label="Priority"
            inputMode="numeric"
            hint="Higher sorts first in the app."
            value={form.values.priority}
            error={form.errors.priority}
            onChange={(event) => form.set('priority', event.target.value)}
          />
        </div>

        <datalist id="content-kinds">
          {CONTENT_KINDS.map((kind) => (
            <option key={kind} value={kind} />
          ))}
        </datalist>

        <ToggleField
          label="Published"
          hint="Unpublished items stay here but are not served to the app."
          checked={form.values.is_published}
          onChange={(checked) => form.set('is_published', checked)}
        />

        <button type="submit" className="visually-hidden">
          Save
        </button>
      </form>
    </Overlay>
  )
}
