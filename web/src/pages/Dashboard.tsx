import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusDot } from '@/components/StatusDot'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import type { DashboardData } from '@/lib/types'

const fadeUp = {
  initial: { opacity: 0, y: 12, filter: 'blur(4px)' },
  animate: { opacity: 1, y: 0, filter: 'blur(0px)' },
  transition: { duration: 0.5, ease: [0.32, 0.72, 0, 1] },
}

export function Dashboard() {
  const { data, isLoading } = useApi<{ data: DashboardData }>('/dashboard')

  if (isLoading || !data) return <PageSkeleton />
  const d = data.data

  return (
    <motion.div {...fadeUp} className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight">Dashboard</h2>

      <div className="grid grid-cols-12 gap-4">
        {/* Rules overview - large card */}
        <GlassCard className="col-span-8">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">Rules</h3>
          <div className="grid grid-cols-5 gap-4">
            {(['active', 'dormant', 'candidate', 'merged', 'archived'] as const).map((status) => (
              <div key={status}>
                <p className="font-mono text-2xl font-semibold">{d.rules[status]}</p>
                <p className="text-xs text-text-tertiary capitalize">{status}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 pt-3 border-t border-white/[0.04] flex gap-6 text-sm text-text-secondary">
            <span>Total: <span className="font-mono text-white">{d.rules.total}</span></span>
            <span>Global: <span className="font-mono text-white">{d.rules.global}</span></span>
          </div>
        </GlassCard>

        {/* Injections */}
        <GlassCard className="col-span-4">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">Injections (24h)</h3>
          <p className="font-mono text-3xl font-semibold">{d.injections_24h}</p>
          <p className="text-sm text-text-secondary mt-1">
            HOT: <span className="text-rose-300 font-mono">{d.injections_hot_24h}</span>
          </p>
        </GlassCard>

        {/* Embed server */}
        <GlassCard className="col-span-3">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Embed Server</h3>
          <div className="flex items-center gap-2">
            <StatusDot running={d.embed_server.running} />
            <span className="text-sm">{d.embed_server.running ? 'Running' : 'Stopped'}</span>
          </div>
          {d.embed_server.pid && (
            <p className="text-xs text-text-tertiary mt-2 font-mono">PID {d.embed_server.pid}</p>
          )}
        </GlassCard>

        {/* Gate */}
        <GlassCard className="col-span-3">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Gate</h3>
          <div className="flex items-center gap-2">
            <StatusDot running={d.gate_enabled} />
            <span className="text-sm">{d.gate_enabled ? 'Enabled' : 'Disabled'}</span>
          </div>
        </GlassCard>

        {/* Extract */}
        <GlassCard className="col-span-3">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Extract</h3>
          <p className="font-mono text-2xl font-semibold">{d.extract_pending}</p>
          <p className="text-xs text-text-tertiary">pending jobs</p>
          <p className="text-xs text-text-secondary mt-1">mode: {d.extract_mode}</p>
        </GlassCard>

        {/* Promotion */}
        <GlassCard className="col-span-3">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Promotion</h3>
          <div className="flex items-center gap-2">
            <StatusDot running={d.promotion_enabled} />
            <span className="text-sm">{d.promotion_enabled ? 'Enabled' : 'Disabled'}</span>
          </div>
        </GlassCard>
      </div>
    </motion.div>
  )
}
