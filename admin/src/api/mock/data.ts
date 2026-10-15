/**
 * Seeded fixtures for the in-memory admin API.
 *
 * Deterministic: the same seed produces the same dataset on every reload, so
 * screenshots, counts and the dashboard are stable between runs. Sized to
 * exercise the real constraints — 640 proxies is past the "500+ rows" bar, and
 * the usage log spans 14 days so the bandwidth chart has something to draw.
 */

import { MOCK_STAFF_EMAIL } from './credentials'
import type {
  Chain,
  ContentItem,
  Plan,
  Protocol,
  ProxyDetail,
  ProxyTier,
  Subscription,
  SubscriptionPlatform,
  SubscriptionStatus,
  UploadBatch,
  UsageLog,
  User,
} from '../types'

/** mulberry32 — small, fast, good enough, and reproducible. */
function rng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const random = rng(20261015)

function pick<T>(items: readonly T[]): T {
  const value = items[Math.floor(random() * items.length)]
  if (value === undefined) throw new Error('pick() from an empty list')
  return value
}

function int(min: number, max: number): number {
  return Math.floor(random() * (max - min + 1)) + min
}

function chance(probability: number): boolean {
  return random() < probability
}

/** Fixed "now" keeps relative timestamps stable within a session. */
export const NOW = new Date('2026-10-15T18:40:00.000Z')

function daysAgo(days: number, jitterHours = 0): string {
  const d = new Date(NOW.getTime() - days * 86400000 - jitterHours * 3600000)
  return d.toISOString()
}

function daysAhead(days: number): string {
  return new Date(NOW.getTime() + days * 86400000).toISOString()
}

const COUNTRIES = [
  ['NL', 'Amsterdam'],
  ['DE', 'Frankfurt'],
  ['US', 'Ashburn'],
  ['US', 'Dallas'],
  ['SG', 'Singapore'],
  ['JP', 'Tokyo'],
  ['GB', 'London'],
  ['FR', 'Paris'],
  ['SE', 'Stockholm'],
  ['CA', 'Toronto'],
  ['BR', 'Sao Paulo'],
  ['IN', 'Mumbai'],
  ['AU', 'Sydney'],
  ['CH', 'Zurich'],
  ['PL', 'Warsaw'],
  ['RO', 'Bucharest'],
] as const

const FIRST = [
  'Ayesha', 'Marco', 'Nadia', 'Tomas', 'Priya', 'Elena', 'Kwame', 'Sofia', 'Ivan', 'Mei',
  'Lukas', 'Fatima', 'Daniel', 'Yuki', 'Omar', 'Clara', 'Rahul', 'Ingrid', 'Pablo', 'Anika',
]
const LAST = [
  'Rahman', 'Bianchi', 'Kovac', 'Novak', 'Sharma', 'Petrova', 'Mensah', 'Costa', 'Ivanov', 'Chen',
  'Weber', 'Haddad', 'Okafor', 'Tanaka', 'Farouk', 'Dubois', 'Nair', 'Lindqvist', 'Moreno', 'Bergman',
]
const DOMAINS = ['proton.me', 'gmail.com', 'fastmail.com', 'outlook.com', 'tuta.io', 'mailbox.org']

const SS_METHODS = ['aes-256-gcm', 'chacha20-ietf-poly1305', 'aes-128-gcm', '2022-blake3-aes-256-gcm']

/* ------------------------------------------------------------------ *
 * Plans
 * ------------------------------------------------------------------ */

export const plans: Plan[] = [
  {
    id: 1, code: 'free', name: 'Free', price: '0.00', currency: 'USD', duration_days: 0,
    description: 'One hop, free-tier proxies, 5 GiB a month.',
    max_chain_length: 1, max_proxy_tier: 'free', max_devices: 1,
    bandwidth_limit_bytes: 5 * 1024 ** 3, is_free_tier: true, is_active: true,
    sort_order: 0, play_product_id: null,
  },
  {
    id: 2, code: 'starter_m', name: 'Starter (monthly)', price: '4.99', currency: 'USD', duration_days: 30,
    description: 'Two hops, premium pool, two devices.',
    max_chain_length: 2, max_proxy_tier: 'premium', max_devices: 2,
    bandwidth_limit_bytes: 200 * 1024 ** 3, is_free_tier: false, is_active: true,
    sort_order: 10, play_product_id: 'pc_starter_monthly',
  },
  {
    id: 3, code: 'pro_m', name: 'Pro (monthly)', price: '9.99', currency: 'USD', duration_days: 30,
    description: 'Three hops, unmetered, four devices.',
    max_chain_length: 3, max_proxy_tier: 'premium', max_devices: 4,
    bandwidth_limit_bytes: null, is_free_tier: false, is_active: true,
    sort_order: 20, play_product_id: 'pc_pro_monthly',
  },
  {
    id: 4, code: 'pro_y', name: 'Pro (annual)', price: '89.99', currency: 'USD', duration_days: 365,
    description: 'Pro, billed yearly.',
    max_chain_length: 3, max_proxy_tier: 'premium', max_devices: 4,
    bandwidth_limit_bytes: null, is_free_tier: false, is_active: true,
    sort_order: 30, play_product_id: 'pc_pro_annual',
  },
  {
    id: 5, code: 'legacy_beta', name: 'Legacy beta', price: '0.00', currency: 'USD', duration_days: 90,
    description: 'Closed beta grant. Not purchasable.',
    max_chain_length: 3, max_proxy_tier: 'premium', max_devices: 2,
    bandwidth_limit_bytes: null, is_free_tier: false, is_active: false,
    sort_order: 90, play_product_id: null,
  },
]

/* ------------------------------------------------------------------ *
 * Users
 * ------------------------------------------------------------------ */

export const users: User[] = []

const USER_COUNT = 243

for (let i = 0; i < USER_COUNT; i += 1) {
  const first = pick(FIRST)
  const last = pick(LAST)
  const joinedDaysAgo = i < 18 ? int(0, 6) : int(7, 540)
  const isStaff = i < 4
  users.push({
    id: i + 1,
    email: `${first.toLowerCase()}.${last.toLowerCase()}${i}@${pick(DOMAINS)}`,
    first_name: first,
    last_name: last,
    is_active: chance(0.93),
    is_staff: isStaff,
    is_superuser: i === 0,
    date_joined: daysAgo(joinedDaysAgo, int(0, 23)),
    last_login: chance(0.82) ? daysAgo(int(0, 30), int(0, 23)) : null,
    subscription: null,
  })
}

// A stable, obvious operator account for the mock login screen.
users[0] = {
  ...users[0]!,
  email: MOCK_STAFF_EMAIL,
  first_name: 'Ops',
  last_name: 'Admin',
  is_active: true,
  is_staff: true,
  is_superuser: true,
  last_login: daysAgo(0, 2),
}

/* ------------------------------------------------------------------ *
 * Subscriptions
 * ------------------------------------------------------------------ */

export const subscriptions: Subscription[] = []

users.forEach((user, index) => {
  // Not every user has one — the free tier is implicit for the rest.
  if (!chance(0.78)) return
  const plan = chance(0.42) ? plans[0]! : pick([plans[1]!, plans[2]!, plans[3]!])
  const isFree = plan.is_free_tier
  const roll = random()
  let status: SubscriptionStatus
  if (isFree) status = 'active'
  else if (roll < 0.63) status = 'active'
  else if (roll < 0.88) status = 'expired'
  else if (roll < 0.96) status = 'cancelled'
  else status = 'pending'

  const startedDaysAgo = int(1, 400)
  const platform: SubscriptionPlatform = isFree ? 'manual' : chance(0.72) ? 'play' : 'stripe'
  const id = subscriptions.length + 1

  subscriptions.push({
    id,
    user: { id: user.id, email: user.email },
    plan: { id: plan.id, code: plan.code, name: plan.name },
    started_at: daysAgo(startedDaysAgo, int(0, 23)),
    expires_at:
      plan.duration_days === 0
        ? null
        : status === 'active'
          ? daysAhead(int(1, plan.duration_days))
          : daysAgo(int(1, 120)),
    status,
    platform,
    platform_txn_id: platform === 'manual' ? null : `${platform}_${(1_000_000 + index * 7919).toString(36)}`,
    last_verified_at: status === 'active' ? daysAgo(int(0, 3), int(0, 23)) : daysAgo(int(5, 90)),
  })

  users[index] = {
    ...user,
    subscription: {
      id,
      plan_code: plan.code,
      plan_name: plan.name,
      status,
      expires_at: subscriptions[subscriptions.length - 1]!.expires_at,
    },
  }
})

/* ------------------------------------------------------------------ *
 * Upload batches
 * ------------------------------------------------------------------ */

export const uploadBatches: UploadBatch[] = []

const BATCH_NAMES = [
  'oxylabs-residential-oct.csv',
  'smartproxy-dc-eu.csv',
  'brightdata-premium-2026-10.csv',
  'ss-nodes-selfhosted.csv',
  'socks5-asia-refresh.csv',
  'http-pool-us-east.csv',
  'failover-backup-set.csv',
  'oxylabs-residential-sep.csv',
  'manual-additions.csv',
]

BATCH_NAMES.forEach((filename, index) => {
  const rowCount = int(40, 220)
  const failCount = chance(0.55) ? int(1, 9) : 0
  const successCount = rowCount - failCount
  const createdCount = Math.round(successCount * (0.4 + random() * 0.6))
  uploadBatches.push({
    id: index + 1,
    filename,
    uploaded_by: MOCK_STAFF_EMAIL,
    row_count: rowCount,
    success_count: successCount,
    created_count: createdCount,
    updated_count: successCount - createdCount,
    fail_count: failCount,
    errors: Array.from({ length: failCount }, () => ({
      line: int(2, rowCount + 1),
      reason: pick([
        'port must be an integer between 1 and 65535',
        'unknown protocol "sock5" — expected one of ss, socks5, http',
        'duplicate host:port in this file',
        'missing required column "country"',
        'ss_method is required when protocol is ss',
        'host is not a valid IP address or hostname',
      ]),
    })).sort((a, b) => a.line - b.line).map((e, k) => ({ ...e, line: e.line + k })),
    created_at: daysAgo(index * 4 + int(0, 3), int(0, 23)),
  })
})

uploadBatches.sort((a, b) => b.created_at.localeCompare(a.created_at))

/* ------------------------------------------------------------------ *
 * Proxies
 * ------------------------------------------------------------------ */

export const proxies: ProxyDetail[] = []

const PROXY_COUNT = 640
const PROTOCOL_WEIGHTS: Protocol[] = [
  'socks5', 'socks5', 'socks5', 'socks5', 'socks5',
  'http', 'http', 'http',
  'ss', 'ss',
]

for (let i = 0; i < PROXY_COUNT; i += 1) {
  const protocol = pick(PROTOCOL_WEIGHTS)
  const [country, city] = pick(COUNTRIES)
  const tier: ProxyTier = chance(0.28) ? 'free' : 'premium'
  const isAlive = chance(tier === 'free' ? 0.72 : 0.91)
  const requiresCredentials = protocol === 'ss' ? true : chance(0.65)
  const port =
    protocol === 'ss' ? pick([8388, 8389, 9000, 443]) : protocol === 'http' ? pick([8080, 3128, 8888, 80]) : pick([1080, 1081, 9050, 1088])

  proxies.push({
    id: i + 1,
    protocol,
    host: `${int(5, 223)}.${int(0, 255)}.${int(0, 255)}.${int(1, 254)}`,
    port,
    country,
    city: chance(0.9) ? city : null,
    tier,
    is_alive: isAlive,
    is_enabled: isAlive ? chance(0.95) : chance(0.45),
    latency_ms: isAlive ? int(28, 940) : null,
    last_checked: chance(0.97) ? daysAgo(0, int(0, 40)) : null,
    requires_credentials: requiresCredentials,
    upload_batch: chance(0.88) ? pick(uploadBatches).id : null,
    username: requiresCredentials && protocol !== 'ss' ? `pc_${(100000 + i * 37).toString(36)}` : null,
    password: requiresCredentials ? `${(random() * 1e16).toString(36).slice(0, 18)}` : null,
    ss_method: protocol === 'ss' ? pick(SS_METHODS) : null,
  })
}

/* ------------------------------------------------------------------ *
 * Content
 * ------------------------------------------------------------------ */

export const content: ContentItem[] = [
  ['welcome_banner', 'announcement', 'Welcome to Proxy Crack', 'Chain up to three proxies and route every app on your device.', 100, true],
  ['udp_warning', 'announcement', 'HTTP hops are TCP-only', 'Adding an HTTP hop to a chain disables UDP. Video calls, games and QUIC will not pass through. Use SOCKS5 or Shadowsocks hops if you need UDP.', 90, true],
  ['latency_expectation', 'tip', 'Why three hops feel slower', 'Every hop adds a full round trip. A three-hop chain typically lands between 400 and 900 ms. That is the cost of the extra layers, not a fault.', 80, true],
  ['faq_kill_switch', 'faq', 'What does the kill switch do?', 'It blocks all traffic if the tunnel drops, so nothing leaks to your normal connection while reconnecting.', 70, true],
  ['faq_always_on', 'faq', 'Can I keep the VPN always on?', 'Yes. Enable Always-on VPN in Android settings and pick Proxy Crack as the provider.', 69, true],
  ['faq_devices', 'faq', 'How many devices can I use?', 'Depends on your plan. Free allows one, Starter two, Pro four.', 68, true],
  ['faq_refund', 'faq', 'How do refunds work?', 'Purchases made through Google Play are refunded by Google under their policy.', 67, true],
  ['privacy_policy', 'legal', 'Privacy policy', 'We keep connection timestamps and byte counters for billing. We do not log destinations.', 50, true],
  ['terms', 'legal', 'Terms of service', 'Use of this service for unlawful activity terminates the account without refund.', 49, true],
  ['dmca', 'legal', 'Abuse and DMCA', 'Reports are handled within five business days.', 48, true],
  ['maintenance_notice', 'announcement', 'Scheduled maintenance 19 Oct', 'The Frankfurt pool will be unavailable between 02:00 and 04:00 UTC.', 95, false],
  ['tip_chain_order', 'tip', 'Put Shadowsocks first', 'An encrypted first hop hides the rest of the chain from your local network.', 60, true],
  ['tip_country_pick', 'tip', 'Nearest exit is not always fastest', 'Try two or three exits and compare the measured latency.', 59, true],
  ['beta_feedback', 'announcement', 'Desktop beta', 'The Linux client is in closed beta. Reply to the welcome email to join.', 40, false],
].map(([key, kind, title, body, priority, published], index) => ({
  id: index + 1,
  key: key as string,
  kind: kind as string,
  title: title as string,
  body: body as string,
  priority: priority as number,
  is_published: published as boolean,
  created_at: daysAgo(60 - index * 3, int(0, 23)),
  updated_at: daysAgo(int(0, 30), int(0, 23)),
}))

/* ------------------------------------------------------------------ *
 * Usage logs — 14 days, so the 7-day chart has a real series
 * ------------------------------------------------------------------ */

export const usageLogs: UsageLog[] = []

for (let day = 13; day >= 0; day -= 1) {
  // A weekly rhythm plus a dip mid-window, so the chart is not a flat line.
  const weekday = (NOW.getDay() - day + 70) % 7
  const weekendFactor = weekday === 0 || weekday === 6 ? 0.72 : 1
  const sessions = Math.round(int(34, 58) * weekendFactor * (day === 5 ? 0.55 : 1))
  for (let s = 0; s < sessions; s += 1) {
    const user = pick(users)
    const durationMinutes = int(3, 320)
    const start = new Date(NOW.getTime() - day * 86400000 - int(0, 23) * 3600000 - int(0, 59) * 60000)
    const down = Math.round((50 + random() * 2400) * 1024 * 1024 * (durationMinutes / 120))
    usageLogs.push({
      id: usageLogs.length + 1,
      user: { id: user.id, email: user.email },
      bytes_up: Math.round(down * (0.06 + random() * 0.14)),
      bytes_down: down,
      session_start: start.toISOString(),
      session_end: chance(0.94) ? new Date(start.getTime() + durationMinutes * 60000).toISOString() : null,
    })
  }
}

usageLogs.sort((a, b) => b.session_start.localeCompare(a.session_start))

/* ------------------------------------------------------------------ *
 * Chains
 * ------------------------------------------------------------------ */

export const chains: Chain[] = []

const CHAIN_NAMES = [
  'Daily driver', 'Work', 'Streaming', 'Max privacy', 'Fast exit', 'Torrent', 'Travel',
  'Banking', 'Research', 'Backup',
]

for (let i = 0; i < 72; i += 1) {
  const user = pick(users)
  const hopCount = int(1, 3)
  const used = new Set<number>()
  const hops = Array.from({ length: hopCount }, (_, position) => {
    let proxy = pick(proxies)
    while (used.has(proxy.id)) proxy = pick(proxies)
    used.add(proxy.id)
    return {
      position: position + 1,
      proxy: { id: proxy.id, protocol: proxy.protocol, host: proxy.host, country: proxy.country },
    }
  })
  chains.push({
    id: i + 1,
    user: { id: user.id, email: user.email },
    name: `${pick(CHAIN_NAMES)}${chance(0.3) ? ` ${int(2, 4)}` : ''}`,
    hops,
    created_at: daysAgo(int(0, 180), int(0, 23)),
  })
}

chains.sort((a, b) => b.created_at.localeCompare(a.created_at))

