import { Eye, EyeOff, LogIn, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/errors'
import { MOCK_STAFF_EMAIL, MOCK_STAFF_PASSWORD } from '@/api/mock/credentials'
import { Button } from '@/components/ui/Button'
import { Field } from '@/components/ui/Field'
import { FormError } from '@/components/ui/States'
import { env } from '@/env'
import { useForm } from '@/hooks/useForm'

import { useAuth } from './AuthProvider'

interface LoginValues extends Record<string, unknown> {
  email: string
  password: string
}

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const form = useForm<LoginValues>({ email: '', password: '' })
  const [submitting, setSubmitting] = useState(false)
  const [revealed, setRevealed] = useState(false)

  const redirectTo =
    (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/'

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault()
    if (submitting) return

    const valid = form.validate((values) => ({
      email: values.email.trim() === '' ? 'Enter your email address.' : undefined,
      password: values.password === '' ? 'Enter your password.' : undefined,
    }))
    if (!valid) return

    setSubmitting(true)
    try {
      await login(form.values.email.trim(), form.values.password)
      navigate(redirectTo, { replace: true })
    } catch (error) {
      /*
       * The backend answers 403 to EVERY login failure — unknown email, wrong
       * password, and correct-password-but-not-staff are byte-identical by
       * design, so the response cannot be used to enumerate accounts. The UI
       * must not undo that: one message for every rejection, and no
       * distinction between "wrong password" and "no such user".
       *
       * Only transport failures (the API is unreachable) are reported
       * differently, because that is about the console, not the account.
       */
      if (error instanceof ApiError) {
        form.applyError(
          new ApiError(error.status, error.code, 'Email or password is incorrect, or the account is not permitted to sign in here.'),
        )
      } else {
        form.applyError(error)
      }
      form.set('password', '')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login">
      <div className="login__card">
        <div>
          <div className="login__brand">
            <ShieldCheck size={22} className="login__mark" aria-hidden="true" />
            <span className="sidebar__name" style={{ fontSize: 'var(--text-md)' }}>
              Proxy Crack
            </span>
          </div>
          <h1 className="login__title" style={{ marginTop: 'var(--space-md)' }}>
            Operations console
          </h1>
          <p className="login__subtitle">Staff accounts only.</p>
        </div>

        <form className="login__form" onSubmit={onSubmit} noValidate>
          <FormError message={form.formError} />

          <Field label="Email" error={form.errors.email} required>
            {(aria) => (
              <input
                {...aria}
                className="input"
                type="email"
                name="email"
                autoComplete="username"
                autoFocus
                value={form.values.email}
                onChange={(event) => form.set('email', event.target.value)}
              />
            )}
          </Field>

          <Field label="Password" error={form.errors.password} required>
            {(aria) => (
              <div className="input-wrap">
                <input
                  {...aria}
                  className="input"
                  style={{ paddingRight: '2.25rem' }}
                  type={revealed ? 'text' : 'password'}
                  name="password"
                  autoComplete="current-password"
                  value={form.values.password}
                  onChange={(event) => form.set('password', event.target.value)}
                />
                <button
                  type="button"
                  className="input-wrap__clear"
                  onClick={() => setRevealed((current) => !current)}
                  aria-pressed={revealed}
                >
                  {revealed ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                  <span className="visually-hidden">
                    {revealed ? 'Hide password' : 'Show password'}
                  </span>
                </button>
              </div>
            )}
          </Field>

          <Button
            type="submit"
            variant="primary"
            size="lg"
            block
            loading={submitting}
            icon={<LogIn size={15} />}
            style={{ marginTop: 'var(--space-2xs)' }}
          >
            Sign in
          </Button>

          {env.useMockApi && (
            <p className="login__hint">
              Mock API is enabled. Sign in with <code>{MOCK_STAFF_EMAIL}</code> /{' '}
              <code>{MOCK_STAFF_PASSWORD}</code>. Set <code>VITE_USE_MOCK_API=false</code> to
              use the Django backend.
            </p>
          )}
        </form>

        <p className="login__foot">
          API: <span className="mono">{env.useMockApi ? 'in-memory mock' : env.apiBaseUrl}</span>
        </p>
      </div>
    </main>
  )
}
