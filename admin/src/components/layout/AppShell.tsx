import { FlaskConical, LogOut, Menu, ShieldCheck, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '@/auth/AuthProvider'
import { Button } from '@/components/ui/Button'
import { useConfirm } from '@/components/ui/ConfirmProvider'
import { env } from '@/env'
import { fullName, initials } from '@/lib/format'

import { NAV, titleForPath } from './nav'

export function AppShell() {
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)

  // Any navigation closes the mobile drawer; otherwise it covers the result.
  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <div className="main">
        <header className="header">
          <Button
            className="header__menu"
            variant="quiet"
            iconOnly
            icon={menuOpen ? <X size={17} /> : <Menu size={17} />}
            aria-expanded={menuOpen}
            aria-controls="sidebar-nav"
            onClick={() => setMenuOpen((current) => !current)}
          >
            {menuOpen ? 'Close navigation' : 'Open navigation'}
          </Button>

          <h1 className="header__title truncate">{titleForPath(location.pathname)}</h1>
          <span className="spacer" />
          <UserMenu />
        </header>

        <main className="page" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <nav className="sidebar" id="sidebar-nav" data-open={open} aria-label="Sections">
      <div className="sidebar__brand">
        <ShieldCheck size={20} className="sidebar__mark" aria-hidden="true" />
        <span className="sidebar__wordmark">
          <span className="sidebar__name">Proxy Crack</span>
          <span className="sidebar__sub">Operations</span>
        </span>
        <span className="spacer" />
        <Button
          className="header__menu"
          variant="quiet"
          size="sm"
          iconOnly
          icon={<X size={16} />}
          onClick={onClose}
        >
          Close navigation
        </Button>
      </div>

      <div className="sidebar__nav">
        {NAV.map((group) => (
          <div key={group.label}>
            <div className="sidebar__group">{group.label}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className="nav-link"
                title={item.label}
              >
                <item.icon size={16} className="nav-link__icon" aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </div>

      {env.useMockApi && (
        <div className="sidebar__footer">
          <span className="env-chip" title="Serving data from the in-memory contract mock">
            <FlaskConical size={13} aria-hidden="true" />
            <span>Mock API</span>
          </span>
        </div>
      )}
    </nav>
  )
}

function UserMenu() {
  const { user, logout } = useAuth()
  const confirm = useConfirm()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent): void {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!user) return null

  async function onLogout(): Promise<void> {
    setOpen(false)
    const confirmed = await confirm({
      title: 'Sign out?',
      body: 'Your session token will be blacklisted and you will return to the login screen.',
      confirmLabel: 'Sign out',
      tone: 'primary',
    })
    if (confirmed) await logout()
  }

  return (
    <div className="user-menu" ref={containerRef}>
      <button
        type="button"
        className="user-menu__trigger"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="avatar" aria-hidden="true">
          {initials(user)}
        </span>
        <span className="user-menu__name truncate">{fullName(user)}</span>
      </button>

      {open && (
        <div className="user-menu__panel" role="menu">
          <div className="user-menu__identity">
            <div className="user-menu__email">{user.email}</div>
            <div className="user-menu__role">
              {user.is_superuser ? 'Superuser' : user.is_staff ? 'Staff' : 'No admin role'}
            </div>
          </div>
          <button type="button" className="menu-item menu-item--danger" role="menuitem" onClick={onLogout}>
            <LogOut size={15} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
