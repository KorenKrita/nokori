import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'

interface ConfigData {
  data: Record<string, unknown>
}

interface HealthData {
  data: Record<string, { status: string; detail: string }>
}

export function Config() {
  const { data: config, isLoading: l1 } = useApi<ConfigData>('/config')
  const { data: health, isLoading: l2 } = useApi<HealthData>('/health')

  if (l1 || l2) return <PageSkeleton />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6"
    >
      <h2 className="text-2xl font-semibold tracking-tight">Config & Health</h2>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">
          Health Checks
        </h3>
        <div className="space-y-2">
          {Object.entries(health?.data ?? {}).map(([key, check]) => (
            <div key={key} className="flex items-center justify-between py-1">
              <span className="text-sm text-text-secondary">{key}</span>
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-text-tertiary max-w-[300px] truncate">
                  {check.detail}
                </span>
                <StatusBadge status={check.status} />
              </div>
            </div>
          ))}
        </div>
      </GlassCard>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">
          Active Configuration
        </h3>
        <div className="space-y-1">
          {Object.entries(config?.data ?? {}).map(([key, value]) => (
            <div key={key} className="flex justify-between py-1 border-b border-white/[0.02]">
              <span className="text-sm text-text-secondary">{key}</span>
              <span className="text-xs font-mono text-text-tertiary">
                {value === null ? 'null' : String(value)}
              </span>
            </div>
          ))}
        </div>
      </GlassCard>
    </motion.div>
  )
}
