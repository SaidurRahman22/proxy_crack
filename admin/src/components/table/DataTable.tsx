import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'
import { useRef, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import { ErrorState, SkeletonRows } from '../ui/States'

export interface Column<T> {
  key: string
  header: string
  /** Field name sent as `?ordering=`. Omit to make the column unsortable. */
  sortKey?: string
  align?: 'left' | 'right'
  width?: string
  /** Shrink to content and never wrap. */
  tight?: boolean
  className?: string
  render: (row: T) => ReactNode
  /** Column header text for screen readers when `header` is empty. */
  srHeader?: string
}

export interface SelectionApi {
  selected: ReadonlySet<number>
  toggle: (id: number) => void
  setMany: (ids: number[], selected: boolean) => void
}

interface DataTableProps<T> {
  caption: string
  columns: Array<Column<T>>
  rows: T[]
  rowKey: (row: T) => number
  /** First load — renders skeleton rows so the layout does not jump. */
  isLoading: boolean
  /** Background refetch — dims the body instead of blanking it. */
  isFetching?: boolean
  error?: unknown
  onRetry?: () => void
  empty: ReactNode
  ordering?: string
  onOrderingChange?: (ordering: string) => void
  selection?: SelectionApi
  onRowClick?: (row: T) => void
  /**
   * Windowed rendering. Only the proxy table turns this on: with page sizes up
   * to 200 and a sticky header inside a scroll container it keeps the DOM at
   * ~25 rows regardless of the page size.
   */
  virtualize?: boolean
  /** Total matching rows, for `aria-rowcount`. */
  totalCount?: number
}

const ROW_HEIGHT = 44

export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  isLoading,
  isFetching = false,
  error,
  onRetry,
  empty,
  ordering,
  onOrderingChange,
  selection,
  onRowClick,
  virtualize = false,
  totalCount,
}: DataTableProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null)

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
    enabled: virtualize,
  })

  if (error) {
    return <ErrorState error={error} onRetry={onRetry} />
  }

  const columnCount = columns.length + (selection ? 1 : 0)
  const allIds = rows.map(rowKey)
  const selectedOnPage = selection ? allIds.filter((id) => selection.selected.has(id)).length : 0
  const allSelected = selectedOnPage > 0 && selectedOnPage === allIds.length
  const someSelected = selectedOnPage > 0 && !allSelected

  const virtualRows = virtualize ? virtualizer.getVirtualItems() : null
  const paddingTop = virtualRows && virtualRows.length > 0 ? (virtualRows[0]?.start ?? 0) : 0
  const paddingBottom =
    virtualRows && virtualRows.length > 0
      ? virtualizer.getTotalSize() - (virtualRows[virtualRows.length - 1]?.end ?? 0)
      : 0

  const body = (row: T, index: number) => {
    const id = rowKey(row)
    const isSelected = selection?.selected.has(id) ?? false
    return (
      <tr
        key={id}
        aria-rowindex={index + 2}
        aria-selected={selection ? isSelected : undefined}
        data-selected={isSelected || undefined}
        data-clickable={onRowClick ? 'true' : undefined}
        onClick={onRowClick ? () => onRowClick(row) : undefined}
        style={virtualize ? { height: ROW_HEIGHT } : undefined}
      >
        {selection && (
          <td className="cell--checkbox" onClick={(event) => event.stopPropagation()}>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => selection.toggle(id)}
              />
              <span className="visually-hidden">Select row {id}</span>
            </label>
          </td>
        )}
        {columns.map((column) => (
          <td
            key={column.key}
            className={cn(
              column.align === 'right' && 'cell--num',
              column.tight && 'cell--tight',
              column.className,
            )}
          >
            {column.render(row)}
          </td>
        ))}
      </tr>
    )
  }

  return (
    <div
      className={cn('table-wrap', virtualize && 'table-wrap--virtual')}
      ref={scrollRef}
      tabIndex={virtualize ? 0 : undefined}
      aria-label={virtualize ? `${caption} (scrollable)` : undefined}
    >
      <table
        className={cn('table', isFetching && !isLoading && 'table--loading')}
        aria-rowcount={totalCount ?? rows.length}
        aria-busy={isLoading || isFetching}
      >
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {selection && (
              <th scope="col" className="cell--checkbox">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(node) => {
                      if (node) node.indeterminate = someSelected
                    }}
                    disabled={allIds.length === 0}
                    onChange={() => selection.setMany(allIds, !allSelected)}
                  />
                  <span className="visually-hidden">
                    {allSelected ? 'Clear selection' : 'Select all rows on this page'}
                  </span>
                </label>
              </th>
            )}
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(column.align === 'right' && 'cell--num', column.tight && 'cell--tight')}
                style={column.width ? { width: column.width } : undefined}
                aria-sort={
                  column.sortKey && ordering
                    ? ordering === column.sortKey
                      ? 'ascending'
                      : ordering === `-${column.sortKey}`
                        ? 'descending'
                        : 'none'
                    : undefined
                }
              >
                {column.sortKey && onOrderingChange ? (
                  <SortButton
                    label={column.header || (column.srHeader ?? column.key)}
                    sortKey={column.sortKey}
                    ordering={ordering}
                    onChange={onOrderingChange}
                  />
                ) : (
                  (column.header || <span className="visually-hidden">{column.srHeader ?? column.key}</span>)
                )}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {isLoading && <SkeletonRows rows={8} columns={columnCount} />}

          {!isLoading && rows.length === 0 && (
            <tr>
              <td colSpan={columnCount} style={{ height: 'auto' }}>
                {empty}
              </td>
            </tr>
          )}

          {!isLoading && virtualRows !== null && (
            <>
              {paddingTop > 0 && (
                <tr aria-hidden="true">
                  <td colSpan={columnCount} style={{ height: paddingTop, padding: 0, border: 0 }} />
                </tr>
              )}
              {virtualRows.map((virtualRow) => {
                const row = rows[virtualRow.index]
                return row === undefined ? null : body(row, virtualRow.index)
              })}
              {paddingBottom > 0 && (
                <tr aria-hidden="true">
                  <td colSpan={columnCount} style={{ height: paddingBottom, padding: 0, border: 0 }} />
                </tr>
              )}
            </>
          )}

          {!isLoading && virtualRows === null && rows.map((row, index) => body(row, index))}
        </tbody>
      </table>
    </div>
  )
}

function SortButton({
  label,
  sortKey,
  ordering,
  onChange,
}: {
  label: string
  sortKey: string
  ordering: string | undefined
  onChange: (ordering: string) => void
}) {
  const ascending = ordering === sortKey
  const descending = ordering === `-${sortKey}`
  const active = ascending || descending

  return (
    <button
      type="button"
      className="th-sort"
      data-active={active || undefined}
      onClick={() => onChange(ascending ? `-${sortKey}` : sortKey)}
    >
      {label}
      {descending ? (
        <ArrowDown size={12} className="th-sort__icon" aria-hidden="true" />
      ) : ascending ? (
        <ArrowUp size={12} className="th-sort__icon" aria-hidden="true" />
      ) : (
        <ChevronsUpDown size={12} className="th-sort__icon" aria-hidden="true" />
      )}
      <span className="visually-hidden">
        {active ? `sorted ${ascending ? 'ascending' : 'descending'}` : 'not sorted'}
      </span>
    </button>
  )
}
