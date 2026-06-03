import { NavLink, Outlet } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { cn } from '@/lib/utils'
import { t } from '@/lib/i18n'
import { LocaleSwitcher } from '@/components/LocaleSwitcher'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
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
  { to: '/', key: 'nav.dashboard', icon: ChartBar },
  { to: '/rules', key: 'nav.rules', icon: ListBullets },
  { to: '/retrieve', key: 'nav.retrieve', icon: MagnifyingGlass },
  { to: '/injections', key: 'nav.injections', icon: Syringe },
  { to: '/extract', key: 'nav.extract', icon: FunnelSimple },
  { to: '/lifecycle', key: 'nav.lifecycle', icon: ArrowsClockwise },
  { to: '/config', key: 'nav.config', icon: GearSix },
  { to: '/logs', key: 'nav.logs', icon: Terminal },
]

export function Layout() {
  return (
    <div className="flex min-h-[100dvh]">
      <aside className="fixed left-0 top-0 h-full w-60 border-r border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] backdrop-blur-xl p-4 flex flex-col z-10">
        <div className="px-3 py-4 mb-4">
          <motion.h1
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
            className="text-lg font-semibold tracking-tight"
          >
            Nokori
          </motion.h1>
          <p className="text-xs text-text-tertiary mt-0.5">Web Dashboard</p>
        </div>
        <nav className="flex flex-col gap-0.5 flex-1">
          {NAV_ITEMS.map(({ to, key, icon: Icon }, index) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]',
                  isActive
                    ? 'text-[var(--color-nav-active-text)]'
                    : 'text-text-secondary hover:text-[var(--color-nav-hover-text)] hover:bg-[var(--color-pill-hover-bg)]'
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.div
                      layoutId="nav-active-pill"
                      className="absolute inset-0 rounded-lg bg-[var(--color-nav-active-bg)] border-l-2 border-accent-sky"
                      transition={{ type: 'spring', stiffness: 500, damping: 35 }}
                    />
                  )}
                  <motion.div
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.4, delay: index * 0.05, ease: [0.32, 0.72, 0, 1] }}
                    className="relative flex items-center gap-3"
                  >
                    <Icon size={18} weight="light" />
                    {t(key)}
                  </motion.div>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto pt-4 border-t border-[var(--color-border-subtle)] space-y-2">
          <ThemeSwitcher />
          <LocaleSwitcher />
        </div>
      </aside>
      <main className="ml-60 flex-1 p-6">
        <div className="max-w-[1400px] mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
