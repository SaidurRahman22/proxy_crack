import { useQuery } from '@tanstack/react-query'
import { Check, Search } from 'lucide-react'
import { useId, useRef, useState } from 'react'

import { api } from '@/api/client'
import type { User } from '@/api/types'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { fullName } from '@/lib/format'

interface UserPickerProps {
  label: string
  selected: User | null
  onSelect: (user: User | null) => void
  error?: string
  hint?: string
  required?: boolean
  full?: boolean
}

/**
 * Searchable user selector following the ARIA combobox pattern.
 *
 * A plain `<select>` is not an option here — the user list runs to thousands
 * of rows, so the control has to query the API as you type. Arrow keys move
 * the active option, Enter picks it, Escape closes.
 */
export function UserPicker({
  label,
  selected,
  onSelect,
  error,
  hint,
  required,
  full,
}: UserPickerProps) {
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const debounced = useDebouncedValue(term)
  const id = useId()
  const listId = `${id}-list`
  const errorId = `${id}-error`
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const query = useQuery({
    queryKey: ['users', 'picker', debounced],
    queryFn: ({ signal }) =>
      api.users.list({ search: debounced, page_size: 25, ordering: 'email' }, signal),
    enabled: open,
    staleTime: 30_000,
  })

  const results = query.data?.results ?? []

  function choose(user: User): void {
    onSelect(user)
    setTerm('')
    setOpen(false)
  }

  return (
    <div className={full ? 'field field--full' : 'field'}>
      <label className="field__label" htmlFor={id}>
        {label}
        {required && (
          <span className="field__required" aria-hidden="true">
            *
          </span>
        )}
      </label>

      {selected ? (
        <div className="picker-selected">
          <span className="truncate">
            <strong>{selected.email}</strong>
            <span className="muted"> · {fullName(selected)}</span>
          </span>
          <button
            type="button"
            className="btn btn--quiet btn--sm"
            onClick={() => {
              onSelect(null)
              setOpen(true)
            }}
          >
            Change
          </button>
        </div>
      ) : (
        <div className="picker">
          <div className="input-wrap">
            <Search size={15} className="input-wrap__icon" aria-hidden="true" />
            <input
              id={id}
              className="input input--with-icon"
              role="combobox"
              aria-expanded={open}
              aria-controls={listId}
              aria-autocomplete="list"
              aria-invalid={Boolean(error)}
              aria-describedby={error ? errorId : undefined}
              aria-activedescendant={open && results[activeIndex] ? `${id}-opt-${activeIndex}` : undefined}
              autoComplete="off"
              placeholder="Search by email or name"
              value={term}
              onFocus={() => setOpen(true)}
              onBlur={() => {
                // Let a click on an option land before the list unmounts.
                blurTimer.current = setTimeout(() => setOpen(false), 140)
              }}
              onChange={(event) => {
                setTerm(event.target.value)
                setActiveIndex(0)
                setOpen(true)
              }}
              onKeyDown={(event) => {
                if (event.key === 'ArrowDown') {
                  event.preventDefault()
                  setOpen(true)
                  setActiveIndex((current) => Math.min(results.length - 1, current + 1))
                } else if (event.key === 'ArrowUp') {
                  event.preventDefault()
                  setActiveIndex((current) => Math.max(0, current - 1))
                } else if (event.key === 'Enter') {
                  const user = results[activeIndex]
                  if (open && user) {
                    event.preventDefault()
                    choose(user)
                  }
                } else if (event.key === 'Escape') {
                  setOpen(false)
                }
              }}
            />
          </div>

          {open && (
            <ul className="picker__list" id={listId} role="listbox" aria-label={label}>
              {query.isPending && <li className="picker__empty">Searching…</li>}
              {!query.isPending && results.length === 0 && (
                <li className="picker__empty">No accounts match “{debounced}”.</li>
              )}
              {results.map((user, index) => (
                <li
                  key={user.id}
                  id={`${id}-opt-${index}`}
                  role="option"
                  aria-selected={index === activeIndex}
                  className="picker__option"
                  data-active={index === activeIndex || undefined}
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseDown={(event) => {
                    event.preventDefault()
                    if (blurTimer.current) clearTimeout(blurTimer.current)
                    choose(user)
                  }}
                >
                  <span className="truncate">{user.email}</span>
                  <span className="muted truncate picker__option-sub">{fullName(user)}</span>
                  {index === activeIndex && <Check size={14} aria-hidden="true" />}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {error && (
        <p className="field__error" id={errorId}>
          {error}
        </p>
      )}
      {hint && !error && <p className="field__hint">{hint}</p>}
    </div>
  )
}
