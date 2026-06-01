import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusDot } from '@/components/StatusDot'
import { EmbedControl } from '@/components/EmbedControl'
import { AnimatedNumber } from '@/components/AnimatedNumber'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { t } from '@/lib/i18n'
import type { DashboardData } from '@/lib/types'

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } },
}

const cardVariant = {
  hidden: { opacity: 0, y: 20, filter: 'blur(8px)', scale: 0.96 },
  show: {
    opacity: 1,
    y: 0,
    filter: 'blur(0px)',
    scale: 1,
    transition: { duration: 0.6, ease: [0.32, 0.72, 0, 1] },
  },
}

export function Dashboard() {
  const { data, isLoading, refetch } = useApi<{ data: DashboardData }>('/dashboard')

  if (isLoading || !data) return <PageSkeleton />
  const d = data.data

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="space-y-6">
      <motion.h2
        variants={cardVariant}
        className="text-2xl font-semibold tracking-tight"
      >
        {t('dashboard.title')}
      </motion.h2>

      <motion.div variants={stagger} className="grid grid-cols-12 gap-4">
        {/* Rules overview */}
        <motion.div variants={cardVariant} className="col-span-8">
          <GlassCard hover>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">
              {t('dashboard.rules')}
            </h3>
            <div className="grid grid-cols-5 gap-4">
              {(['active', 'dormant', 'candidate', 'merged', 'archived'] as const).map((status) => (
                <motion.div
                  key={status}
                  whileHover={{ scale: 1.08, y: -2 }}
                  transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
                  className="cursor-default"
                >
                  <AnimatedNumber value={d.rules[status]} className="font-mono text-2xl font-semibold" />
                  <p className="text-xs text-text-tertiary capitalize">{status}</p>
                </motion.div>
              ))}
            </div>
            <div className="mt-4 pt-3 border-t border-white/[0.04] flex gap-6 text-sm text-text-secondary">
              <span>{t('dashboard.total')}: <span className="font-mono text-white">{d.rules.total}</span></span>
              <span>{t('dashboard.global')}: <span className="font-mono text-white">{d.rules.global}</span></span>
            </div>
          </GlassCard>
        </motion.div>

        {/* Injections */}
        <motion.div variants={cardVariant} className="col-span-4">
          <GlassCard hover>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">
              {t('dashboard.injections_24h')}
            </h3>
            <AnimatedNumber value={d.injections_24h} className="font-mono text-4xl font-semibold block" />
            <p className="text-sm text-text-secondary mt-2">
              HOT: <span className="text-rose-300 font-mono">{d.injections_hot_24h}</span>
            </p>
          </GlassCard>
        </motion.div>

        {/* Embed server with control */}
        <motion.div variants={cardVariant} className="col-span-4">
          <GlassCard hover>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
              {t('dashboard.embed_server')}
            </h3>
            <EmbedControl
              running={d.embed_server.running}
              pid={d.embed_server.pid}
              onAction={refetch}
            />
          </GlassCard>
        </motion.div>

        {/* Gate */}
        <motion.div variants={cardVariant} className="col-span-2">
          <GlassCard hover>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
              {t('dashboard.gate')}
            </h3>
            <div className="flex items-center gap-2">
              <StatusDot running={d.gate_enabled} />
              <span className="text-sm">{d.gate_enabled ? t('dashboard.enabled') : t('dashboard.disabled')}</span>
            </div>
          </GlassCard>
        </motion.div>

        {/* Extract */}
        <motion.div variants={cardVariant} className="col-span-3">
          <GlassCard hover>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
              {t('dashboard.extract')}
            </h3>
            <AnimatedNumber value={d.extract_pending} className="font-mono text-2xl font-semibold block" />
            <p className="text-xs text-text-tertiary">{t('dashboard.pending_jobs')}</p>
          </GlassCard>
        </motion.div>

        {/* Promotion */}
        <motion.div variants={cardVariant} className="col-span-3">
          <GlassCard hover>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
              {t('dashboard.promotion')}
            </h3>
            <div className="flex items-center gap-2">
              <StatusDot running={d.promotion_enabled} />
              <span className="text-sm">{d.promotion_enabled ? t('dashboard.enabled') : t('dashboard.disabled')}</span>
            </div>
          </GlassCard>
        </motion.div>
      </motion.div>
    </motion.div>
  )
}
