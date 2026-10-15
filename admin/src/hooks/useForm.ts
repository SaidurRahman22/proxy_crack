import { useCallback, useState } from 'react'

import { ApiError, describeError } from '@/api/errors'

export type FieldErrors<T> = Partial<Record<keyof T & string, string>>

export interface FormApi<T extends Record<string, unknown>> {
  values: T
  errors: FieldErrors<T>
  formError: string | null
  set: <K extends keyof T & string>(key: K, value: T[K]) => void
  setAll: (values: T) => void
  reset: (values?: T) => void
  /**
   * Maps a thrown value onto the form. A `{error:{detail:{field:[...]}}}`
   * envelope lands on the fields; anything else becomes a form-level banner.
   * Unknown field names are kept in the banner rather than silently dropped.
   */
  applyError: (error: unknown) => void
  /** Runs client-side validation; returns false and shows errors if invalid. */
  validate: (rules: (values: T) => FieldErrors<T>) => boolean
  clearErrors: () => void
}

export function useForm<T extends Record<string, unknown>>(initial: T): FormApi<T> {
  const [values, setValues] = useState<T>(initial)
  const [errors, setErrors] = useState<FieldErrors<T>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const set = useCallback(<K extends keyof T & string>(key: K, value: T[K]) => {
    setValues((current) => ({ ...current, [key]: value }))
    // Editing a field clears its error — stale red text is worse than none.
    setErrors((current) => {
      if (!(key in current)) return current
      const next = { ...current }
      delete next[key]
      return next
    })
  }, [])

  const reset = useCallback(
    (next?: T) => {
      setValues(next ?? initial)
      setErrors({})
      setFormError(null)
    },
    [initial],
  )

  const applyError = useCallback((error: unknown) => {
    if (!(error instanceof ApiError)) {
      setFormError(describeError(error))
      return
    }

    const mapped = error.fieldErrors
    if (Object.keys(mapped).length === 0) {
      setFormError(error.message)
      return
    }

    const fields: FieldErrors<T> = {}
    const leftovers: string[] = []
    for (const [field, message] of Object.entries(mapped)) {
      if (field === 'non_field_errors' || field === 'detail') leftovers.push(message)
      else fields[field as keyof T & string] = message
    }
    setErrors(fields)
    setFormError(leftovers.length > 0 ? leftovers.join(' ') : null)
  }, [])

  const validate = useCallback(
    (rules: (current: T) => FieldErrors<T>) => {
      // Rules are written as `{ email: cond ? 'message' : undefined }`, so the
      // undefined entries are still own keys and must be stripped before the
      // object is treated as "has errors" — otherwise every form fails
      // validation while rendering no message at all.
      const found: FieldErrors<T> = {}
      for (const [field, message] of Object.entries(rules(values))) {
        if (typeof message === 'string' && message !== '') {
          found[field as keyof T & string] = message
        }
      }
      setErrors(found)
      if (Object.keys(found).length > 0) {
        setFormError(null)
        return false
      }
      return true
    },
    [values],
  )

  const clearErrors = useCallback(() => {
    setErrors({})
    setFormError(null)
  }, [])

  return { values, errors, formError, set, setAll: setValues, reset, applyError, validate, clearErrors }
}
