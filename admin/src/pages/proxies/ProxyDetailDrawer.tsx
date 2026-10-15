import { Eye, EyeOff, KeyRound, Pencil, ShieldAlert } from 'lucide-react'

import { ApiError } from '@/api/errors'
import type { ProxyListItem } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { AliveStatus, EnabledStatus, ProtocolChip, TierBadge } from '@/components/ui/Indicators'
import { Overlay } from '@/components/ui/Overlay'
import { Banner, ErrorState, LoadingState } from '@/components/ui/States'
import { EM_DASH, formatDateTime, formatLatency, formatRelative } from '@/lib/format'

import { useProxyCredentials } from './useProxyCredentials'

export function ProxyDetailDrawer({
  proxy,
  onClose,
  onEdit,
}: {
  proxy: ProxyListItem | null
  onClose: () => void
  onEdit: (proxy: ProxyListItem) => void
}) {
  const secrets = useProxyCredentials(proxy?.id ?? null)

  /*
   * There is no background query for the detail record here, and that is
   * deliberate. `GET /proxies/{id}/` returns decrypted credentials in the same
   * payload as the metadata, and TanStack Query caches the RAW response — a
   * `select` that strips secrets changes what the component sees, not what the
   * cache holds. So the drawer renders from the list row (which the contract
   * guarantees carries no credentials) and hits the detail endpoint only when
   * the operator explicitly asks to reveal, via `useProxyCredentials`, which
   * keeps the plaintext in component state and nowhere else.
   */

  return (
    <Overlay
      kind="drawer"
      open={proxy !== null}
      onClose={() => {
        secrets.forget()
        onClose()
      }}
      title={proxy ? `${proxy.host}:${proxy.port}` : ''}
      subtitle={proxy ? `${proxy.protocol} · ${proxy.country}${proxy.city ? ` · ${proxy.city}` : ''}` : ''}
      footer={
        proxy && (
          <Button variant="secondary" icon={<Pencil size={14} />} onClick={() => onEdit(proxy)}>
            Edit this proxy
          </Button>
        )
      }
    >
      {proxy && (
        <>
          <dl className="kv">
            <dt>Protocol</dt>
            <dd>
              <ProtocolChip protocol={proxy.protocol} />
            </dd>

            <dt>Endpoint</dt>
            <dd className="mono">
              {proxy.host}:{proxy.port}
            </dd>

            <dt>Location</dt>
            <dd>
              {proxy.country}
              {proxy.city ? ` · ${proxy.city}` : ''}
            </dd>

            <dt>Tier</dt>
            <dd>
              <TierBadge tier={proxy.tier} />
            </dd>

            <dt>Health</dt>
            <dd>
              <AliveStatus alive={proxy.is_alive} />
            </dd>

            <dt>Availability</dt>
            <dd>
              <EnabledStatus enabled={proxy.is_enabled} />
            </dd>

            <dt>Latency</dt>
            <dd className="num">{formatLatency(proxy.latency_ms)}</dd>

            <dt>Last checked</dt>
            <dd title={proxy.last_checked ?? undefined}>{formatRelative(proxy.last_checked)}</dd>

            <dt>Upload batch</dt>
            <dd>
              {proxy.upload_batch === null ? (
                <span className="muted">Added manually</span>
              ) : (
                <span className="mono">#{proxy.upload_batch}</span>
              )}
            </dd>
          </dl>

          <section className="stack" style={{ gap: 'var(--space-xs)' }}>
            <h3 className="eyebrow">
              <KeyRound size={12} aria-hidden="true" style={{ display: 'inline', verticalAlign: '-1px' }} />{' '}
              Credentials
            </h3>

            {!proxy.requires_credentials ? (
              <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                This proxy does not use credentials.
              </p>
            ) : secrets.status === 'idle' ? (
              <>
                <Banner tone="warning" icon={<ShieldAlert size={15} aria-hidden="true" />}>
                  Revealing decrypts the stored credentials and writes an audit log entry on the
                  server. They are held in this panel only and are discarded when it closes.
                </Banner>
                <div>
                  <Button variant="secondary" icon={<Eye size={14} />} onClick={() => void secrets.reveal()}>
                    Reveal credentials
                  </Button>
                </div>
              </>
            ) : secrets.status === 'loading' ? (
              <LoadingState label="Decrypting" />
            ) : secrets.status === 'error' ? (
              <ErrorState
                error={secrets.error}
                // Reveals are throttled at 120/hour and 400/day. Hitting that
                // cap is an expected operational event, so name it plainly and
                // do not offer a retry that would just burn another request.
                title={
                  secrets.error instanceof ApiError && secrets.error.status === 429
                    ? 'Credential reveal limit reached'
                    : 'Could not read the credentials'
                }
                onRetry={
                  secrets.error instanceof ApiError && secrets.error.status === 429
                    ? undefined
                    : () => void secrets.reveal()
                }
              />
            ) : (
              <>
                <dl className="kv">
                  <dt>Username</dt>
                  <dd>
                    <Secret value={secrets.credentials?.username ?? null} />
                  </dd>
                  <dt>Password</dt>
                  <dd>
                    <Secret value={secrets.credentials?.password ?? null} />
                  </dd>
                  {secrets.credentials?.ss_method && (
                    <>
                      <dt>SS method</dt>
                      <dd className="mono">{secrets.credentials.ss_method}</dd>
                    </>
                  )}
                </dl>
                <div>
                  <Button variant="ghost" size="sm" icon={<EyeOff size={14} />} onClick={secrets.forget}>
                    Hide again
                  </Button>
                </div>
              </>
            )}
          </section>

          {proxy.last_checked && (
            <p className="muted" style={{ fontSize: 'var(--text-xs)' }}>
              Health last measured {formatDateTime(proxy.last_checked)}.
            </p>
          )}
        </>
      )}
    </Overlay>
  )
}

function Secret({ value }: { value: string | null }) {
  if (value === null || value === '') return <span className="muted">{EM_DASH}</span>
  return (
    <span className="secret">
      {/*
        No copy-to-clipboard button by design: the clipboard outlives this
        panel and is readable by every other app on the machine.
      */}
      <code className="secret__value">{value}</code>
    </span>
  )
}
