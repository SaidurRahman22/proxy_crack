import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { api } from '@/api/client'
import type { Protocol, ProxyListItem, ProxyTier, ProxyWrite } from '@/api/types'
import { PROTOCOLS, PROXY_TIERS } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { SelectField, TextField, ToggleField } from '@/components/ui/Field'
import { Banner, FormError } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { Overlay } from '@/components/ui/Overlay'
import { useForm } from '@/hooks/useForm'

const PROTOCOL_OPTIONS = PROTOCOLS.map((protocol) => ({
  value: protocol,
  label:
    protocol === 'ss' ? 'Shadowsocks (ss)' : protocol === 'socks5' ? 'SOCKS5' : 'HTTP (TCP only)',
}))

const TIER_OPTIONS = PROXY_TIERS.map((tier) => ({
  value: tier,
  label: tier[0]!.toUpperCase() + tier.slice(1),
}))

const SS_METHODS = [
  'aes-256-gcm',
  'chacha20-ietf-poly1305',
  'aes-128-gcm',
  '2022-blake3-aes-256-gcm',
].map((method) => ({ value: method, label: method }))

interface ProxyFormValues extends Record<string, unknown> {
  protocol: Protocol
  host: string
  port: string
  country: string
  city: string
  tier: ProxyTier
  is_enabled: boolean
  username: string
  password: string
  ss_method: string
}

function toValues(proxy: ProxyListItem | null): ProxyFormValues {
  return {
    protocol: proxy?.protocol ?? 'socks5',
    host: proxy?.host ?? '',
    port: proxy ? String(proxy.port) : '',
    country: proxy?.country ?? '',
    city: proxy?.city ?? '',
    tier: proxy?.tier ?? 'premium',
    is_enabled: proxy?.is_enabled ?? true,
    // Never pre-filled, even when editing: that would be a silent reveal.
    username: '',
    password: '',
    ss_method: '',
  }
}

export function ProxyFormDrawer({
  open,
  proxy,
  onClose,
}: {
  open: boolean
  proxy: ProxyListItem | null
  onClose: () => void
}) {
  const form = useForm<ProxyFormValues>(toValues(proxy))
  const queryClient = useQueryClient()
  const toast = useToast()
  const isEdit = proxy !== null

  const seed = `${proxy?.id ?? 'new'}:${open}`
  const [lastSeed, setLastSeed] = useState(seed)
  if (seed !== lastSeed) {
    setLastSeed(seed)
    form.reset(toValues(proxy))
  }

  const mutation = useMutation({
    mutationFn: (values: ProxyFormValues) => {
      const payload: ProxyWrite = {
        protocol: values.protocol,
        host: values.host.trim(),
        port: Number(values.port),
        country: values.country.trim().toUpperCase(),
        city: values.city.trim() === '' ? null : values.city.trim(),
        tier: values.tier,
        is_enabled: values.is_enabled,
        username: values.username.trim() === '' ? null : values.username.trim(),
        password: values.password === '' ? null : values.password,
        ss_method: values.ss_method === '' ? null : values.ss_method,
      }

      if (!isEdit) return api.proxies.create(payload)

      // On edit, omit blank credential fields so an untouched form does not
      // wipe the stored secrets.
      const patch: Partial<ProxyWrite> = { ...payload }
      if (values.username.trim() === '') delete patch.username
      if (values.password === '') delete patch.password
      if (values.ss_method === '') delete patch.ss_method
      return api.proxies.update(proxy.id, patch)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['proxies'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      toast.success(isEdit ? 'Proxy updated' : 'Proxy created', `${form.values.host}:${form.values.port}`)
      onClose()
    },
    onError: (error) => form.applyError(error),
  })

  function onSubmit(event: FormEvent): void {
    event.preventDefault()
    const port = Number(form.values.port)
    const valid = form.validate((values) => ({
      host: values.host.trim() === '' ? 'A host or IP address is required.' : undefined,
      port:
        !Number.isInteger(port) || port < 1 || port > 65535
          ? 'Enter a port between 1 and 65535.'
          : undefined,
      country:
        values.country.trim().length !== 2 ? 'Use a 2-letter ISO country code, e.g. NL.' : undefined,
      ss_method:
        values.protocol === 'ss' && values.ss_method === '' && !isEdit
          ? 'Shadowsocks needs a cipher.'
          : undefined,
    }))
    if (!valid) return
    mutation.mutate(form.values)
  }

  const isShadowsocks = form.values.protocol === 'ss'
  const isHttp = form.values.protocol === 'http'

  return (
    <Overlay
      kind="drawer"
      open={open}
      onClose={onClose}
      busy={mutation.isPending}
      title={isEdit ? 'Edit proxy' : 'New proxy'}
      subtitle={isEdit ? `${proxy.host}:${proxy.port}` : 'Single entry. Use CSV import for a list.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} loading={mutation.isPending}>
            {isEdit ? 'Save changes' : 'Create proxy'}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="stack">
        <FormError message={form.formError} />

        <div className="form-grid">
          <SelectField
            label="Protocol"
            options={PROTOCOL_OPTIONS}
            value={form.values.protocol}
            error={form.errors.protocol}
            onChange={(event) => form.set('protocol', event.target.value as Protocol)}
          />
          <SelectField
            label="Tier"
            options={TIER_OPTIONS}
            value={form.values.tier}
            error={form.errors.tier}
            onChange={(event) => form.set('tier', event.target.value as ProxyTier)}
          />
          <TextField
            label="Host"
            required
            autoComplete="off"
            spellCheck={false}
            value={form.values.host}
            error={form.errors.host}
            onChange={(event) => form.set('host', event.target.value)}
          />
          <TextField
            label="Port"
            required
            inputMode="numeric"
            value={form.values.port}
            error={form.errors.port}
            onChange={(event) => form.set('port', event.target.value)}
          />
          <TextField
            label="Country"
            required
            maxLength={2}
            hint="ISO 3166-1 alpha-2."
            style={{ textTransform: 'uppercase' }}
            value={form.values.country}
            error={form.errors.country}
            onChange={(event) => form.set('country', event.target.value)}
          />
          <TextField
            label="City"
            value={form.values.city}
            error={form.errors.city}
            onChange={(event) => form.set('city', event.target.value)}
          />
        </div>

        {isHttp && (
          <Banner tone="warning" icon={<ShieldAlert size={15} aria-hidden="true" />}>
            An HTTP hop carries TCP only. Any chain containing one is TCP-only — no QUIC, WebRTC,
            VoIP or most games.
          </Banner>
        )}

        <h3 className="eyebrow">Credentials</h3>
        <Banner tone="info">
          {isEdit
            ? 'Existing credentials are never loaded into this form. Leave a field blank to keep it unchanged.'
            : 'Stored encrypted at rest and only ever decrypted on the detail endpoint.'}
        </Banner>

        <div className="form-grid">
          {!isShadowsocks && (
            <TextField
              label="Username"
              autoComplete="off"
              value={form.values.username}
              error={form.errors.username}
              onChange={(event) => form.set('username', event.target.value)}
            />
          )}
          <TextField
            label="Password"
            type="password"
            autoComplete="new-password"
            value={form.values.password}
            error={form.errors.password}
            onChange={(event) => form.set('password', event.target.value)}
          />
          {isShadowsocks && (
            <SelectField
              label="Shadowsocks cipher"
              full
              placeholder={isEdit ? 'Keep current cipher' : 'Choose a cipher…'}
              options={SS_METHODS}
              value={form.values.ss_method}
              error={form.errors.ss_method}
              onChange={(event) => form.set('ss_method', event.target.value)}
            />
          )}
        </div>

        <ToggleField
          label="Enabled"
          hint="Disabled proxies stay in the pool but are never handed to a client."
          checked={form.values.is_enabled}
          onChange={(checked) => form.set('is_enabled', checked)}
        />

        <button type="submit" className="visually-hidden">
          Save
        </button>
      </form>
    </Overlay>
  )
}
