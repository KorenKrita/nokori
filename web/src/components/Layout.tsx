import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  ChartBar,
  ListBullets,
  MagnifyingGlass,
  Syringe,
  FunnelSimple,
  ArrowsClockwise,
  GearSix,
  Terminal,
} from '@phosphor-icons/react'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: ChartBar },
  { to: '/rules', label: 'Rules', icon: ListBullets },
  { to: '/retrieve', label: 'Retrieve', icon: MagnifyingGlass },
  { to: '/injections', label: 'Injections', icon: Syringe },
  { to: '/extract', label: 'Extract', icon: FunnelSimple },
  { to: '/lifecycle', label: 'Lifecycle', icon: ArrowsClockwise },
  { to: '/config', label: 'Config', icon: GearSix },
  { to: '/logs', label: 'Logs', icon: Terminal },
]

export function Layout() {
  return (
    <div className="flex min-h-[100dvh]">
      <aside className="fixed left-0 top-0 h-full w-60 border-r border-white/[0.06] bg-white/[0.02] backdrop-blur-xl p-4 flex flex-col gap-1 z-10">
        <div className="px-3 py-4 mb-4">
          <h1 className="text-lg font-semibold tracking-tight">Nokori</h1>
          <p className="text-xs text-text-tertiary mt-0.5">Web Dashboard</p>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]',
                  isActive
                    ? 'bg-white/[0.08] text-white border-l-2 border-accent-sky'
                    : 'text-text-secondary hover:text-white hover:bg-white/[0.04]'
                )
              }
            >
              <Icon size={18} weight="light" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="ml-60 flex-1 p-6">
        <div className="max-w-[1400px] mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
