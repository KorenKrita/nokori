import { useState } from 'react'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { t } from '@/lib/i18n'
import type { Injection, Meta } from '@/lib/types'

export function Injections() {
  const [levelFilter, setLevelFilter] = useState('')
  const { data, isLoading } = useApi<{ data: Injection[]; meta: Meta }>(
    '/injections',
    { level: levelFilter }
  )

  if (isLoading) return <PageSkeleton />

  const injections = data?.data ?? []
  const meta = data?.meta

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">{t('injections.title')}</h2>
        <span className="text-sm text-text-tertiary font-mono">{meta?.total ?? 0} total</span>
      </div>

      <div className="flex gap-2">
        {[
          { value: '', label: t('injections.filter.all') },
          { value: 'hot', label: t('injections.filter.hot') },
          { value: 'warm', label: t('injections.filter.warm') },
        ].map((f) => (
          <button
            key={f.value}
            onClick={() => setLevelFilter(f.value)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
              levelFilter === f.value
                ? 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]'
                : 'text-text-secondary hover:text-[var(--color-nav-hover-text)] hover:bg-[var(--color-pill-hover-bg)]'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wider text-text-tertiary border-b border-[var(--color-border-subtle)]">
                <th className="text-left py-3 px-2">{t('injections.col.rule')}</th>
                <th className="text-left py-3 px-2">{t('injections.col.level')}</th>
                <th className="text-left py-3 px-2">{t('injections.col.session')}</th>
                <th className="text-left py-3 px-2">{t('injections.col.time')}</th>
              </tr>
            </thead>
            <tbody>
              {injections.map((inj) => (
                <tr key={inj.id} className="border-b border-[var(--color-border-subtle)] hover:bg-[var(--color-row-hover)]">
                  <td className="py-3 px-2 font-mono text-accent-sky">
                    {inj.rule_short_id ?? inj.rule_id.slice(0, 8)}
                  </td>
                  <td className="py-3 px-2"><StatusBadge status={inj.level} /></td>
                  <td className="py-3 px-2 text-text-tertiary font-mono text-xs">{inj.session_id.slice(0, 16)}</td>
                  <td className="py-3 px-2 text-text-tertiary text-xs">{inj.created_at}</td>
                </tr>
              ))}
              {injections.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-text-tertiary">{t('injections.no_results')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </motion.div>
  )
}
