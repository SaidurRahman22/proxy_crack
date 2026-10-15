import {
  Activity,
  FileText,
  Layers,
  LayoutDashboard,
  Network,
  Receipt,
  Upload,
  Users,
  Waypoints,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  /** Exact match only — otherwise `/` would match everything. */
  end?: boolean
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV: NavGroup[] = [
  {
    label: 'Overview',
    items: [{ to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true }],
  },
  {
    label: 'Accounts',
    items: [
      { to: '/users', label: 'Users', icon: Users },
      { to: '/plans', label: 'Plans', icon: Layers },
      { to: '/subscriptions', label: 'Subscriptions', icon: Receipt },
    ],
  },
  {
    label: 'Network',
    items: [
      { to: '/proxies', label: 'Proxies', icon: Network },
      { to: '/upload', label: 'CSV import', icon: Upload },
      { to: '/upload-batches', label: 'Upload batches', icon: Activity },
    ],
  },
  {
    label: 'Insight',
    items: [
      { to: '/usage-logs', label: 'Usage logs', icon: Activity },
      { to: '/chains', label: 'User chains', icon: Waypoints },
      { to: '/content', label: 'Content', icon: FileText },
    ],
  },
]

const ALL_ITEMS = NAV.flatMap((group) => group.items)

/** Longest matching prefix wins, so `/proxies/12` resolves to "Proxies". */
export function titleForPath(pathname: string): string {
  if (pathname === '/') return 'Dashboard'
  const match = ALL_ITEMS.filter((item) => item.to !== '/' && pathname.startsWith(item.to)).sort(
    (a, b) => b.to.length - a.to.length,
  )[0]
  return match?.label ?? 'Proxy Crack'
}
