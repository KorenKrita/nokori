import { motion, AnimatePresence } from 'motion/react'
import { CaretDown } from '@phosphor-icons/react'
import { GlassCard } from '@/components/GlassCard'
import { cn } from '@/lib/utils'
import { t } from '@/lib/i18n'

interface Props {
  id: string
  label: string
  collapsed: boolean
  onToggle: () => void
  hover?: boolean
  fieldCount: number
  children: React.ReactNode
}

export function ConfigSectionCard({
  id,
  label,
  collapsed,
  onToggle,
  hover = true,
  fieldCount,
  children,
}: Props) {
  return (
    <GlassCard hover={hover && !collapsed}>
      <button
        type="button"
        className="w-full flex items-center justify-between gap-3 text-left -mt-1 mb-0"
        onClick={onToggle}
        aria-expanded={!collapsed}
        aria-controls={`config-section-body-${id}`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)] truncate">{label}</h3>
          <span className="text-[10px] font-mono text-text-tertiary shrink-0">{fieldCount}</span>
        </div>
        <span className="flex items-center gap-1 shrink-0 text-xs text-text-tertiary">
          {collapsed ? t('config.section_expand') : t('config.section_collapse')}
          <CaretDown
            size={16}
            weight="bold"
            className={cn('transition-transform duration-300', !collapsed && 'rotate-180')}
          />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            id={`config-section-body-${id}`}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="pt-1">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </GlassCard>
  )
}
