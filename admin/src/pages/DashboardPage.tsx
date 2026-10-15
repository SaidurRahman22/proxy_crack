import { useQuery } from '@tanstack/react-query'
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  CheckCircle2,
  Clock,
  Network,
  Receipt,
  RefreshCw,
  Upload,
  Users,
  XCircle,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import type { Stats } from '@/api/types'
import { HorizontalBars, ProportionMeter } from '@/components/charts/Bars'
import { ChartLegend, TimeSeriesChart } from '@/components/charts/TimeSeriesChart'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { StatSplit, StatTile } from '@/components/ui/StatTile'
import { ErrorState, LoadingState } from '@/components/ui/States'
import {
  formatBytes,
  formatNumber,
  formatPercent,
  formatRelative,
  pluralise,
  share,
} from '@/lib/format'

import { useBandwidthSeries, useSignupSeries } from './useDerivedSeries'

/* Fixed assignment from the validated categorical palette. Never cycled. */
const SERIES = {
  ss: '#3987E5',
  socks5: '#D95926',
  http: '#9085E9',
  up: '#3987E5',
  down: '#9085E9',
} as const

const ALIVE = '#22C55E'
const DEAD = '#F87171'
const NEUTRAL = '#475569'

export function DashboardPage() {
  const stats = useQuery({
    queryKey: ['stats'],
    queryFn: ({ signal }) => api.stats.get(signal),
    staleTime: 20_000,
  })

  if (stats.isPending) return <LoadingState label="Loading dashboard" />
  if (stats.isError) {
    return <ErrorState error={stats.error} title="Could not load the dashboard" onRetry={() => stats.refetch()} />
  }

  return <Dashboard stats={stats.data} isFetching={stats.isFetching} onRefresh={() => stats.refetch()} />
}

function Dashboard({
  stats,
  isFetching,
  onRefresh,
}: {
  stats: Stats
  isFetching: boolean
  onRefresh: () => void
}) {
  const bandwidth = useBandwidthSeries(7)
  const signups = useSignupSeries(14)

  const aliveShare = share(stats.proxies.alive, stats.proxies.total)
  const enabledDead = stats.proxies.dead
  const totalTraffic = stats.usage.bytes_up_7d + stats.usage.bytes_down_7d

  return (
    <>
      <PageHeader
        title="Operations overview"
        description="Fleet health, account movement and seven days of traffic."
        actions={
          <Button
            variant="ghost"
            icon={<RefreshCw size={14} />}
            loading={isFetching}
            onClick={onRefresh}
          >
            Refresh
          </Button>
        }
      />

      <div className="dash">
        {/* What needs doing, before what is merely true. */}
        <div className="span-12">
          <AttentionStrip stats={stats} aliveShare={aliveShare} />
        </div>

        <div className="span-3">
          <StatTile
            label="Proxy fleet"
            icon={<Network size={13} aria-hidden="true" />}
            value={formatPercent(aliveShare, 1)}
            unit="alive"
            hero
          >
            <ProportionMeter
              showLegend={false}
              segments={[
                { key: 'alive', label: 'Alive', value: stats.proxies.alive, color: ALIVE },
                { key: 'dead', label: 'Dead', value: stats.proxies.dead, color: DEAD },
              ]}
            />
            <StatSplit
              items={[
                {
                  key: 'alive',
                  label: 'Alive',
                  value: (
                    <span className="status status--alive" style={{ fontSize: 'inherit' }}>
                      <CheckCircle2 size={13} aria-hidden="true" />
                      {formatNumber(stats.proxies.alive)}
                    </span>
                  ),
                },
                {
                  key: 'dead',
                  label: 'Dead',
                  value: (
                    <span className="status status--dead" style={{ fontSize: 'inherit' }}>
                      <XCircle size={13} aria-hidden="true" />
                      {formatNumber(stats.proxies.dead)}
                    </span>
                  ),
                },
                { key: 'total', label: 'Total', value: formatNumber(stats.proxies.total) },
              ]}
            />
          </StatTile>
        </div>

        <div className="span-3">
          <StatTile
            label="Users"
            icon={<Users size={13} aria-hidden="true" />}
            value={formatNumber(stats.users.total)}
            delta={{ value: stats.users.new_7d, label: '7d' }}
          >
            <StatSplit
              items={[
                { key: 'staff', label: 'Staff', value: formatNumber(stats.users.staff) },
                { key: 'new', label: 'New this week', value: formatNumber(stats.users.new_7d) },
              ]}
            />
          </StatTile>
        </div>

        <div className="span-3">
          <StatTile
            label="Subscriptions"
            icon={<Receipt size={13} aria-hidden="true" />}
            value={formatNumber(stats.subscriptions.active)}
            unit="active"
          >
            <ProportionMeter
              showLegend={false}
              segments={[
                { key: 'active', label: 'Active', value: stats.subscriptions.active, color: ALIVE },
                { key: 'expired', label: 'Expired', value: stats.subscriptions.expired, color: DEAD },
              ]}
            />
            <StatSplit
              items={[
                { key: 'paid', label: 'Paid', value: formatNumber(stats.subscriptions.paid) },
                { key: 'free', label: 'Free tier', value: formatNumber(stats.subscriptions.free_tier) },
                { key: 'expired', label: 'Expired', value: formatNumber(stats.subscriptions.expired) },
              ]}
            />
          </StatTile>
        </div>

        <div className="span-3">
          <StatTile
            label="Traffic, last 7 days"
            icon={<ArrowDownToLine size={13} aria-hidden="true" />}
            value={formatBytes(totalTraffic, 0)}
          >
            <StatSplit
              items={[
                {
                  key: 'down',
                  label: 'Downstream',
                  value: (
                    <span className="row" style={{ gap: '0.3em' }}>
                      <ArrowDownToLine size={13} aria-hidden="true" style={{ color: SERIES.down }} />
                      {formatBytes(stats.usage.bytes_down_7d, 0)}
                    </span>
                  ),
                },
                {
                  key: 'up',
                  label: 'Upstream',
                  value: (
                    <span className="row" style={{ gap: '0.3em' }}>
                      <ArrowUpFromLine size={13} aria-hidden="true" style={{ color: SERIES.up }} />
                      {formatBytes(stats.usage.bytes_up_7d, 0)}
                    </span>
                  ),
                },
                { key: 'sessions', label: 'Sessions', value: formatNumber(stats.usage.sessions_7d) },
              ]}
            />
          </StatTile>
        </div>

        {/* Up and down share a scale, so they share one chart. Never two axes. */}
        <section className="card span-8">
          <div className="card__header">
            <div>
              <h3 className="card__title">Bandwidth, last 7 days</h3>
              <p className="dialog__subtitle">
                Bucketed by day from usage logs
                {bandwidth.data?.truncated
                  ? ` · sampled ${formatNumber(bandwidth.data.sampled)} of ${formatNumber(bandwidth.data.total)} sessions`
                  : ''}
              </p>
            </div>
            <ChartLegend
              series={[
                { key: 'down', label: 'Down', color: SERIES.down },
                { key: 'up', label: 'Up', color: SERIES.up },
              ]}
            />
          </div>
          <div className="card__body">
            {bandwidth.isPending && <LoadingState label="Building the series" />}
            {bandwidth.isError && (
              <ErrorState
                error={bandwidth.error}
                title="Could not build the bandwidth series"
                onRetry={() => bandwidth.refetch()}
              />
            )}
            {bandwidth.data && (
              <TimeSeriesChart
                ariaLabel="Upstream and downstream bytes per day for the last seven days"
                formatValue={(value) => formatBytes(value, 0)}
                series={[
                  { key: 'down', label: 'Down', color: SERIES.down, points: bandwidth.data.down },
                  { key: 'up', label: 'Up', color: SERIES.up, points: bandwidth.data.up },
                ]}
              />
            )}
          </div>
        </section>

        {/* Categorical breakdown: horizontal bars, sorted, direct-labelled. */}
        <section className="card span-4">
          <div className="card__header">
            <h3 className="card__title">Proxies by protocol</h3>
            <Link className="btn btn--quiet btn--sm" to="/proxies">
              View all
            </Link>
          </div>
          <div className="card__body">
            <HorizontalBars
              total={stats.proxies.total}
              data={[
                {
                  key: 'socks5',
                  label: 'socks5',
                  value: stats.proxies.by_protocol.socks5,
                  color: SERIES.socks5,
                  to: '/proxies?protocol=socks5',
                },
                {
                  key: 'http',
                  label: 'http',
                  value: stats.proxies.by_protocol.http,
                  color: SERIES.http,
                  to: '/proxies?protocol=http',
                },
                {
                  key: 'ss',
                  label: 'ss',
                  value: stats.proxies.by_protocol.ss,
                  color: SERIES.ss,
                  to: '/proxies?protocol=ss',
                },
              ]}
            />
            <hr
              style={{
                border: 0,
                borderTop: '1px solid var(--border)',
                margin: 'var(--space-md) 0',
              }}
            />
            <h4 className="eyebrow" style={{ marginBottom: 'var(--space-xs)' }}>
              By tier
            </h4>
            <ProportionMeter
              segments={[
                { key: 'premium', label: 'Premium', value: stats.proxies.premium, color: SERIES.ss },
                { key: 'free', label: 'Free', value: stats.proxies.free, color: NEUTRAL },
              ]}
            />
          </div>
        </section>

        <section className="card span-6">
          <div className="card__header">
            <div>
              <h3 className="card__title">Signups, last 14 days</h3>
              <p className="dialog__subtitle">
                Bucketed by day from the user list
                {signups.data?.truncated ? ' · series may be incomplete' : ''}
              </p>
            </div>
          </div>
          <div className="card__body">
            {signups.isPending && <LoadingState label="Building the series" />}
            {signups.isError && (
              <ErrorState
                error={signups.error}
                title="Could not build the signup series"
                onRetry={() => signups.refetch()}
              />
            )}
            {signups.data && (
              <TimeSeriesChart
                ariaLabel="New user accounts per day for the last fourteen days"
                formatValue={(value) => formatNumber(Math.round(value))}
                series={[
                  { key: 'signups', label: 'Signups', color: ALIVE, points: signups.data.points },
                ]}
              />
            )}
          </div>
        </section>

        <section className="card span-6">
          <div className="card__header">
            <h3 className="card__title">Last CSV import</h3>
            <Link className="btn btn--quiet btn--sm" to="/upload-batches">
              All batches
            </Link>
          </div>
          <div className="card__body">
            {stats.uploads.last_batch_at === null ? (
              <div className="stack" style={{ gap: 'var(--space-sm)' }}>
                <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                  No proxy list has been imported yet.
                </p>
                <div>
                  <Link className="btn btn--primary btn--sm" to="/upload">
                    <Upload size={14} aria-hidden="true" />
                    Import a CSV
                  </Link>
                </div>
              </div>
            ) : (
              <div className="stack" style={{ gap: 'var(--space-md)' }}>
                <StatSplit
                  items={[
                    {
                      key: 'when',
                      label: 'Imported',
                      value: formatRelative(stats.uploads.last_batch_at),
                    },
                    {
                      key: 'rows',
                      label: pluralise(stats.uploads.last_batch_rows, 'row'),
                      value: formatNumber(stats.uploads.last_batch_rows),
                    },
                  ]}
                />
                <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                  <Clock size={13} aria-hidden="true" style={{ display: 'inline', verticalAlign: '-2px' }} />{' '}
                  Fleet health is measured by the checker, not by the import. {formatNumber(enabledDead)}{' '}
                  {pluralise(enabledDead, 'proxy', 'proxies')} failed their last check.
                </p>
                <div className="row">
                  <Link className="btn btn--secondary btn--sm" to="/upload">
                    <Upload size={14} aria-hidden="true" />
                    Import another
                  </Link>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </>
  )
}

/**
 * The strip leads with the one number an operator acts on, then only the
 * conditions that are actually wrong — each one a link to the filtered list
 * that fixes it. Nothing here is decoration.
 */
function AttentionStrip({ stats, aliveShare }: { stats: Stats; aliveShare: number }) {
  const healthy = aliveShare >= 0.9
  const degraded = aliveShare < 0.75

  return (
    <div className="attention">
      <span className="attention__lead">
        {degraded ? (
          <XCircle size={16} aria-hidden="true" style={{ color: DEAD }} />
        ) : healthy ? (
          <CheckCircle2 size={16} aria-hidden="true" style={{ color: ALIVE }} />
        ) : (
          <Clock size={16} aria-hidden="true" style={{ color: 'var(--warning)' }} />
        )}
        Fleet {formatPercent(aliveShare, 1)} alive
      </span>

      <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
        {formatNumber(stats.proxies.total)} {pluralise(stats.proxies.total, 'proxy', 'proxies')} ·{' '}
        {formatNumber(stats.users.total)} {pluralise(stats.users.total, 'user')} ·{' '}
        {formatNumber(stats.subscriptions.active)} active
      </span>

      <span className="spacer" />

      <div className="attention__items">
        {stats.proxies.dead > 0 && (
          <Link className="attention-chip attention-chip--danger" to="/proxies?is_alive=false">
            <XCircle size={13} aria-hidden="true" />
            {formatNumber(stats.proxies.dead)} dead
          </Link>
        )}
        {stats.subscriptions.expired > 0 && (
          <Link className="attention-chip attention-chip--warning" to="/subscriptions?status=expired">
            <Clock size={13} aria-hidden="true" />
            {formatNumber(stats.subscriptions.expired)} expired
          </Link>
        )}
        {stats.proxies.dead === 0 && stats.subscriptions.expired === 0 && (
          <span className="attention-chip attention-chip--ok">
            <CheckCircle2 size={13} aria-hidden="true" />
            Nothing needs attention
          </span>
        )}
      </div>
    </div>
  )
}
