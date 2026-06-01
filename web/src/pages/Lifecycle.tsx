import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'

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

interface MaintenanceData {
  data: Record<string, string>
}

export function Lifecycle() {
  const { data: promo, isLoading: l1 } = useApi<PromotionData>('/lifecycle/promotion')
  const { data: maint, isLoading: l2 } = useApi<MaintenanceData>('/lifecycle/maintenance')

  if (l1 || l2) return <PageSkeleton />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6"
    >
      <h2 className="text-2xl font-semibold tracking-tight">Lifecycle</h2>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">
          Promotion Progress
        </h3>
        {!promo?.data.enabled && (
          <p className="text-sm text-text-tertiary">Promotion disabled</p>
        )}
        {promo?.data.enabled && promo.data.candidates.length === 0 && (
          <p className="text-sm text-text-tertiary">No promotion candidates yet</p>
        )}
        {promo?.data.candidates.map((c) => (
          <div key={c.short_id} className="border-b border-white/[0.03] py-3 last:border-0">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-accent-sky">{c.short_id}</span>
              <span className="text-xs text-text-tertiary font-mono">
                {c.progress}/{c.threshold}
              </span>
            </div>
            <p className="text-sm text-text-secondary mt-1 truncate">{c.trigger_text}</p>
            <div className="mt-2 h-1.5 rounded-full bg-white/[0.04] overflow-hidden">
              <div
                className="h-full rounded-full bg-accent-violet transition-all duration-500"
                style={{ width: `${Math.min(100, (c.progress / c.threshold) * 100)}%` }}
              />
            </div>
          </div>
        ))}
      </GlassCard>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">
          Maintenance Jobs
        </h3>
        <div className="space-y-2">
          {Object.entries(maint?.data ?? {}).map(([key, lastRun]) => (
            <div key={key} className="flex justify-between py-1">
              <span className="text-sm text-text-secondary">{key}</span>
              <span className="text-xs font-mono text-text-tertiary">{lastRun}</span>
            </div>
          ))}
          {Object.keys(maint?.data ?? {}).length === 0 && (
            <p className="text-sm text-text-tertiary">No maintenance runs recorded</p>
          )}
        </div>
      </GlassCard>
    </motion.div>
  )
}
