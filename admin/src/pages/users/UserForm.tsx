import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { api } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { User, UserWrite } from '@/api/types'
import { ERROR_CODES } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { TextField, ToggleField } from '@/components/ui/Field'
import { Overlay } from '@/components/ui/Overlay'
import { FormError } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { useForm } from '@/hooks/useForm'

interface UserFormValues extends Record<string, unknown> {
  email: string
  first_name: string
  last_name: string
  password: string
  is_active: boolean
  is_staff: boolean
  is_superuser: boolean
}

function toValues(user: User | null): UserFormValues {
  return {
    email: user?.email ?? '',
    first_name: user?.first_name ?? '',
    last_name: user?.last_name ?? '',
    password: '',
    is_active: user?.is_active ?? true,
    is_staff: user?.is_staff ?? false,
    is_superuser: user?.is_superuser ?? false,
  }
}

export function UserFormDrawer({
  open,
  user,
  onClose,
}: {
  open: boolean
  /** `null` creates. */
  user: User | null
  onClose: () => void
}) {
  const form = useForm<UserFormValues>(toValues(user))
  const queryClient = useQueryClient()
  const toast = useToast()
  const [key, setKey] = useState(0)
  const isEdit = user !== null

  // Re-seed when the drawer is opened for a different record.
  const seed = `${user?.id ?? 'new'}:${open}`
  const [lastSeed, setLastSeed] = useState(seed)
  if (seed !== lastSeed) {
    setLastSeed(seed)
    form.reset(toValues(user))
    setKey((current) => current + 1)
  }

  const mutation = useMutation({
    mutationFn: async (values: UserFormValues) => {
      const payload: UserWrite = {
        email: values.email.trim(),
        first_name: values.first_name.trim(),
        last_name: values.last_name.trim(),
        is_active: values.is_active,
        is_staff: values.is_staff,
        is_superuser: values.is_superuser,
      }
      if (isEdit) return api.users.update(user.id, payload)
      return api.users.create({ ...payload, password: values.password })
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      toast.success(isEdit ? 'User updated' : 'User created', saved.email)
      onClose()
    },
    onError: (error) => {
      // A 403 here means the server declined a privilege change (e.g. a staff
      // member granting themselves superuser). Say that, rather than letting
      // it read as a validation failure on a field.
      if (error instanceof ApiError && error.is(ERROR_CODES.privilegeChangeForbidden)) {
        toast.error('Privilege change refused', error.message)
      }
      form.applyError(error)
    },
  })

  function onSubmit(event: FormEvent): void {
    event.preventDefault()
    const valid = form.validate((values) => ({
      email:
        values.email.trim() === ''
          ? 'An email address is required.'
          : !values.email.includes('@')
            ? 'Enter a valid email address.'
            : undefined,
      password:
        !isEdit && values.password.length < 8
          ? 'Set a password of at least 8 characters.'
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
      title={isEdit ? 'Edit user' : 'New user'}
      subtitle={isEdit ? user.email : 'Creates an account with the password you set below.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} loading={mutation.isPending}>
            {isEdit ? 'Save changes' : 'Create user'}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate key={key} className="stack">
        <FormError message={form.formError} />

        <div className="form-grid">
          <TextField
            label="Email"
            type="email"
            required
            full
            autoComplete="off"
            value={form.values.email}
            error={form.errors.email}
            onChange={(event) => form.set('email', event.target.value)}
          />
          <TextField
            label="First name"
            value={form.values.first_name}
            error={form.errors.first_name}
            onChange={(event) => form.set('first_name', event.target.value)}
          />
          <TextField
            label="Last name"
            value={form.values.last_name}
            error={form.errors.last_name}
            onChange={(event) => form.set('last_name', event.target.value)}
          />
          {!isEdit && (
            <TextField
              label="Password"
              type="password"
              required
              full
              autoComplete="new-password"
              hint="At least 8 characters. It is sent once and never returned by the API."
              value={form.values.password}
              error={form.errors.password}
              onChange={(event) => form.set('password', event.target.value)}
            />
          )}
        </div>

        <ToggleField
          label="Active"
          hint="Inactive accounts cannot sign in anywhere."
          checked={form.values.is_active}
          onChange={(checked) => form.set('is_active', checked)}
        />
        <ToggleField
          label="Staff"
          hint="Required for any access to this admin API."
          checked={form.values.is_staff}
          onChange={(checked) => form.set('is_staff', checked)}
        />
        <ToggleField
          label="Superuser"
          hint="Can grant and revoke staff and superuser on other accounts."
          checked={form.values.is_superuser}
          onChange={(checked) => form.set('is_superuser', checked)}
        />

        <button type="submit" className="visually-hidden">
          {isEdit ? 'Save changes' : 'Create user'}
        </button>
      </form>
    </Overlay>
  )
}

export function SetPasswordDialog({
  user,
  onClose,
}: {
  user: User | null
  onClose: () => void
}) {
  const form = useForm<{ password: string; confirm: string } & Record<string, unknown>>({
    password: '',
    confirm: '',
  })
  const toast = useToast()

  const mutation = useMutation({
    mutationFn: (password: string) => {
      if (!user) throw new Error('No user selected')
      return api.users.setPassword(user.id, password)
    },
    onSuccess: () => {
      toast.success('Password changed', user?.email)
      form.reset({ password: '', confirm: '' })
      onClose()
    },
    onError: (error) => {
      if (error instanceof ApiError && error.is(ERROR_CODES.setPasswordForbidden)) {
        toast.error('Not permitted', error.message)
      }
      form.applyError(error)
    },
  })

  function onSubmit(event: FormEvent): void {
    event.preventDefault()
    const valid = form.validate((values) => ({
      password: values.password.length < 8 ? 'At least 8 characters.' : undefined,
      confirm: values.password !== values.confirm ? 'The two passwords do not match.' : undefined,
    }))
    if (!valid) return
    mutation.mutate(form.values.password)
  }

  return (
    <Overlay
      open={user !== null}
      onClose={onClose}
      busy={mutation.isPending}
      title="Set a new password"
      subtitle={user?.email}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} loading={mutation.isPending}>
            Set password
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="stack">
        <FormError message={form.formError} />
        <TextField
          label="New password"
          type="password"
          required
          autoComplete="new-password"
          value={form.values.password}
          error={form.errors.password}
          onChange={(event) => form.set('password', event.target.value)}
        />
        <TextField
          label="Confirm password"
          type="password"
          required
          autoComplete="new-password"
          value={form.values.confirm}
          error={form.errors.confirm}
          onChange={(event) => form.set('confirm', event.target.value)}
        />
        <button type="submit" className="visually-hidden">
          Set password
        </button>
      </form>
    </Overlay>
  )
}
