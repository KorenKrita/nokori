import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { formatDateTime } from '@/lib/formatDateTime'
import { maintenanceJobLabel, t } from '@/lib/i18n'

interface PromotionData {
  data: {
    enabled: boolean
    candidates: {
      short_id: string
      project_id: string
      trigger_text: string
      shadow_hit_count: number
      unique_projects: string[]
      progress: number
      threshold: number
    }[]
  }
}
interface MaintenanceData { data: Record<string, string> }

function formatLastRun(value: string): string {
  if (!value || value === 'never') return t('lifecycle.last_run_never')
  return formatDateTime(value) || value
}

export function Lifecycle() {
  const { data: promo, isLoading: l1 } = useApi<PromotionData>('/lifecycle/promotion')
  const { data: maint, isLoading: l2 } = useApi<MaintenanceData>('/lifecycle/maintenance')

  if (l1 || l2) return <PageSkeleton />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      className="space-y-6"
    >
      <h2 className="text-2xl font-semibold tracking-tight">{t('lifecycle.title')}</h2>

      <GlassCard hover>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">{t('lifecycle.promotion')}</h3>
        {!promo?.data.enabled && <p className="text-sm text-text-tertiary">{t('lifecycle.promotion_disabled')}</p>}
        {promo?.data.enabled && promo.data.candidates.length === 0 && (
          <p className="text-sm text-text-tertiary">{t('lifecycle.no_candidates')}</p>
        )}
        {promo?.data.candidates.map((c) => (
          <div key={c.short_id} className="border-b border-[var(--color-border-subtle)] py-3 last:border-0">
            <div className="flex items-center justify-between gap-3">
              <Link
                to={`/rules/${c.short_id}`}
                className="font-mono text-xs text-accent-sky hover:underline"
              >
                {c.short_id}
              </Link>
              <span className="text-xs text-text-tertiary font-mono shrink-0">
                {c.progress}/{c.threshold}
              </span>
            </div>
            <p className="text-sm text-text-secondary mt-1 truncate">{c.trigger_text}</p>
            <div className="mt-2 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
              <motion.div
                className="h-full rounded-full bg-accent-violet"
                initial={{ width: 0 }}
                animate={{ width: `${Math.min(100, (c.progress / c.threshold) * 100)}%` }}
                transition={{ duration: 0.8, ease: [0.32, 0.72, 0, 1] as const }}
              />
            </div>
          </div>
        ))}
      </GlassCard>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">{t('lifecycle.maintenance')}</h3>
        <div className="space-y-2">
          {Object.entries(maint?.data ?? {}).map(([key, lastRun]) => (
            <div key={key} className="flex justify-between gap-4 py-1">
              <span className="text-sm text-[var(--color-text-primary)]">{maintenanceJobLabel(key)}</span>
              <span className="text-xs font-mono text-text-tertiary whitespace-nowrap shrink-0">
                {formatLastRun(lastRun)}
              </span>
            </div>
          ))}
          {Object.keys(maint?.data ?? {}).length === 0 && (
            <p className="text-sm text-text-tertiary">{t('lifecycle.no_maintenance')}</p>
          )}
        </div>
      </GlassCard>
    </motion.div>
  )
}
