import { AlertCircle } from 'lucide-react'
import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'

import { cn } from '@/lib/cn'

interface FieldShellProps {
  label: string
  hint?: string
  error?: string
  required?: boolean
  full?: boolean
  children: (props: {
    id: string
    'aria-invalid': boolean
    'aria-describedby': string | undefined
  }) => ReactNode
}

/**
 * Wraps a control with its label, hint and inline error, and wires
 * `aria-invalid` + `aria-describedby` so a screen reader hears the validation
 * message as part of the field rather than as loose text nearby.
 */
export function Field({ label, hint, error, required, full, children }: FieldShellProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ')

  return (
    <div className={cn('field', full && 'field--full')}>
      <label className="field__label" htmlFor={id}>
        {label}
        {required && (
          <span className="field__required" aria-hidden="true">
            *
          </span>
        )}
        {required && <span className="visually-hidden">(required)</span>}
      </label>

      {children({
        id,
        'aria-invalid': Boolean(error),
        'aria-describedby': describedBy === '' ? undefined : describedBy,
      })}

      {error && (
        <p className="field__error" id={errorId}>
          <AlertCircle size={13} aria-hidden="true" style={{ marginTop: 2 }} />
          <span>{error}</span>
        </p>
      )}
      {hint && !error && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  )
}

type TextFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> & {
  label: string
  hint?: string
  error?: string
  full?: boolean
}

export function TextField({ label, hint, error, full, required, ...rest }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} required={required} full={full}>
      {(aria) => <input className="input" required={required} {...aria} {...rest} />}
    </Field>
  )
}

type SelectFieldProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> & {
  label: string
  hint?: string
  error?: string
  full?: boolean
  options: ReadonlyArray<{ value: string; label: string }>
  placeholder?: string
}

export function SelectField({
  label,
  hint,
  error,
  full,
  required,
  options,
  placeholder,
  ...rest
}: SelectFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} required={required} full={full}>
      {(aria) => (
        <select className="select" required={required} {...aria} {...rest}>
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}

type TextAreaFieldProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'> & {
  label: string
  hint?: string
  error?: string
  full?: boolean
}

export function TextAreaField({ label, hint, error, full, required, ...rest }: TextAreaFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} required={required} full={full}>
      {(aria) => <textarea className="textarea" required={required} {...aria} {...rest} />}
    </Field>
  )
}

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode
}

export function Checkbox({ label, className, ...rest }: CheckboxProps) {
  return (
    <label className={cn('checkbox', className)}>
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  )
}

interface ToggleFieldProps {
  label: string
  hint?: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  full?: boolean
}

/** A boolean presented as a labelled row — denser than a card, still legible. */
export function ToggleField({ label, hint, checked, onChange, disabled, full }: ToggleFieldProps) {
  const id = useId()
  const hintId = `${id}-hint`
  return (
    <div className={cn('toggle-field', full && 'field--full')}>
      <div className="toggle-field__text">
        <label className="toggle-field__label" htmlFor={id}>
          {label}
        </label>
        {hint && (
          <span className="toggle-field__hint" id={hintId}>
            {hint}
          </span>
        )}
      </div>
      <label className="checkbox">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          aria-describedby={hint ? hintId : undefined}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="visually-hidden">{label}</span>
      </label>
    </div>
  )
}
