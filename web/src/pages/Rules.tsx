import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { t } from '@/lib/i18n'
import type { Meta, Rule } from '@/lib/types'

export function Rules() {
  const [statusFilter, setStatusFilter] = useState('active,dormant')
  const { data, isLoading } = useApi<{ data: Rule[]; meta: Meta }>(
    '/rules',
    { status: statusFilter }
  )

  if (isLoading) return <PageSkeleton />

  const rules = data?.data ?? []
  const meta = data?.meta

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">{t('rules.title')}</h2>
        <span className="text-sm text-text-tertiary font-mono">
          {t('rules.total', { n: meta?.total ?? 0 })}
        </span>
      </div>

      <div className="flex gap-2">
        {[
          { value: 'active,dormant', label: 'Active' },
          { value: 'active', label: 'Active' },
          { value: 'dormant', label: 'Dormant' },
          { value: 'candidate', label: 'Candidate' },
          { value: 'archived,merged', label: 'Archived' },
          { value: '', label: t('rules.filter.all') },
        ].map((f) => (
          <button
            key={f.value}
            onClick={() => setStatusFilter(f.value)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
              statusFilter === f.value
                ? 'bg-white/10 text-white dark:bg-white/10 dark:text-white'
                : 'text-text-secondary hover:text-white hover:bg-white/[0.04]'
            }`}
          >
            {f.value || t('rules.filter.all')}
          </button>
        ))}
      </div>

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wider text-text-tertiary border-b border-white/[0.04] dark:border-white/[0.04]">
                <th className="text-left py-3 px-2">{t('rules.col.id')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.status')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.type')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.trigger')}</th>
                <th className="text-right py-3 px-2">{t('rules.col.hits')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.scope')}</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr
                  key={rule.id}
                  className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors"
                >
                  <td className="py-3 px-2">
                    <Link
                      to={`/rules/${rule.short_id}`}
                      className="font-mono text-accent-sky hover:underline"
                    >
                      {rule.short_id}
                    </Link>
                  </td>
                  <td className="py-3 px-2"><StatusBadge status={rule.status} /></td>
                  <td className="py-3 px-2 text-text-secondary">{rule.source_type}</td>
                  <td className="py-3 px-2 text-text-secondary max-w-[300px] truncate">{rule.trigger_text}</td>
                  <td className="py-3 px-2 text-right font-mono">{rule.hit_count}</td>
                  <td className="py-3 px-2 text-text-tertiary text-xs">
                    {rule.project_scope === 'global' ? 'global' : rule.project_id?.slice(0, 8) ?? '-'}
                  </td>
                </tr>
              ))}
              {rules.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-text-tertiary">{t('rules.no_results')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </motion.div>
  )
}
