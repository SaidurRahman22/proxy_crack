import { ChevronLeft, ChevronRight, FilterX, Loader2, Search, X } from 'lucide-react'
import { useEffect, useId, useState, type ReactNode } from 'react'

import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { PAGE_SIZES } from '@/hooks/useListQueryState'
import { formatNumber, pluralise } from '@/lib/format'

import { Button } from '../ui/Button'

interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  placeholder: string
  /** Shown while a request triggered by this box is in flight. */
  busy?: boolean
}

/**
 * Debounced search. Local state keeps typing responsive; the debounced value
 * is what reaches the URL and the query.
 */
export function SearchBox({ value, onChange, placeholder, busy = false }: SearchBoxProps) {
  const [draft, setDraft] = useState(value)
  const debounced = useDebouncedValue(draft)
  const id = useId()

  useEffect(() => {
    setDraft(value)
    // Only when the URL changes underneath us (back button, clear filters).
  }, [value])

  useEffect(() => {
    if (debounced !== value) onChange(debounced)
    // `onChange` is stable from useListQueryState; `value` would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])

  return (
    <div className="input-wrap toolbar__search">
      <Search size={15} className="input-wrap__icon" aria-hidden="true" />
      <input
        id={id}
        type="search"
        className="input input--with-icon"
        placeholder={placeholder}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        aria-label={placeholder}
        autoComplete="off"
        spellCheck={false}
      />
      {busy && draft !== '' ? (
        <span className="input-wrap__clear" aria-hidden="true">
          <Loader2 size={14} className="spinner" style={{ border: 0, animation: 'spin 0.8s linear infinite' }} />
        </span>
      ) : draft !== '' ? (
        <button type="button" className="input-wrap__clear" onClick={() => setDraft('')}>
          <X size={14} aria-hidden="true" />
          <span className="visually-hidden">Clear search</span>
        </button>
      ) : null}
    </div>
  )
}

interface FilterSelectProps {
  label: string
  value: string
  onChange: (value: string) => void
  options: ReadonlyArray<{ value: string; label: string }>
  allLabel: string
}

export function FilterSelect({ label, value, onChange, options, allLabel }: FilterSelectProps) {
  const id = useId()
  return (
    <>
      <label className="visually-hidden" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{allLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </>
  )
}

export function Toolbar({
  children,
  actions,
}: {
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="toolbar">
      {children}
      {actions && (
        <>
          <span className="spacer" />
          <div className="toolbar__filters">{actions}</div>
        </>
      )}
    </div>
  )
}

export function ClearFiltersButton({ show, onClear }: { show: boolean; onClear: () => void }) {
  if (!show) return null
  return (
    <Button variant="quiet" size="sm" icon={<FilterX size={14} />} onClick={onClear}>
      Clear
    </Button>
  )
}

interface PaginationProps {
  page: number
  pageSize: number
  count: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  /** Singular noun for the row type, e.g. "proxy". */
  label: string
  /** Supply when the plural is not `label + "s"`, e.g. "proxies". */
  labelPlural?: string
}

export function Pagination({
  page,
  pageSize,
  count,
  onPageChange,
  onPageSizeChange,
  label,
  labelPlural,
}: PaginationProps) {
  const plural = labelPlural ?? `${label}s`
  const pageCount = Math.max(1, Math.ceil(count / pageSize))
  const first = count === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(count, page * pageSize)
  const sizeId = useId()

  return (
    <div className="pagination">
      <span aria-live="polite">
        {count === 0 ? (
          `No ${plural}`
        ) : (
          <>
            <span className="tnum">
              {formatNumber(first)}–{formatNumber(last)}
            </span>{' '}
            of <span className="tnum">{formatNumber(count)}</span> {pluralise(count, label, plural)}
          </>
        )}
      </span>

      <div className="pagination__controls">
        <label className="visually-hidden" htmlFor={sizeId}>
          Rows per page
        </label>
        <select
          id={sizeId}
          className="select"
          style={{ width: 'auto', height: '1.875rem', fontSize: 'var(--text-sm)' }}
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size} / page
            </option>
          ))}
        </select>

        <Button
          variant="ghost"
          size="sm"
          iconOnly
          icon={<ChevronLeft size={15} />}
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous page
        </Button>
        <span className="pagination__page">
          {page} / {pageCount}
        </span>
        <Button
          variant="ghost"
          size="sm"
          iconOnly
          icon={<ChevronRight size={15} />}
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          Next page
        </Button>
      </div>
    </div>
  )
}
