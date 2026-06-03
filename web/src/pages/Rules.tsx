import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { FilterPill } from '@/components/FilterPill'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { t } from '@/lib/i18n'
import type { Meta, Rule } from '@/lib/types'

const STATUS_FILTERS = [
  { value: 'active,dormant', labelKey: 'rules.filter.active_dormant', helpKey: 'rules.filter.help.active_dormant' },
  { value: 'active', labelKey: 'rules.filter.active', helpKey: 'rules.filter.help.active' },
  { value: 'dormant', labelKey: 'rules.filter.dormant', helpKey: 'rules.filter.help.dormant' },
  { value: 'candidate', labelKey: 'rules.filter.candidate', helpKey: 'rules.filter.help.candidate' },
  { value: 'archived,merged', labelKey: 'rules.filter.archived', helpKey: 'rules.filter.help.archived' },
  { value: '', labelKey: 'rules.filter.all', helpKey: 'rules.filter.help.all' },
] as const

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

      <div className="relative z-20 flex flex-wrap gap-2 overflow-visible">
        {STATUS_FILTERS.map((f) => (
          <FilterPill
            key={f.value || '__all__'}
            active={statusFilter === f.value}
            label={t(f.labelKey)}
            help={t(f.helpKey)}
            onClick={() => setStatusFilter(f.value)}
          />
        ))}
      </div>

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-60px' }}
        transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      >
      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm table-fixed">
            <colgroup>
              <col className="w-[4.5rem]" />
              <col className="w-[5.5rem]" />
              <col className="w-[6.5rem]" />
              <col />
              <col className="w-[4.5rem]" />
              <col className="w-[6.5rem]" />
            </colgroup>
            <thead>
              <tr className="text-xs uppercase tracking-wider text-text-tertiary border-b border-[var(--color-border-subtle)]">
                <th className="text-left py-3 px-2">{t('rules.col.id')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.status')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.type')}</th>
                <th className="text-left py-3 px-2">{t('rules.col.trigger')}</th>
                <th className="text-right py-3 pl-2 pr-6">{t('rules.col.hits')}</th>
                <th className="text-left py-3 pl-6 pr-2">{t('rules.col.scope')}</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr
                  key={rule.id}
                  className="border-b border-[var(--color-border-subtle)] hover:bg-[var(--color-row-hover)] transition-colors"
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
                  <td className="py-3 px-2 text-text-secondary truncate">{rule.trigger_text}</td>
                  <td className="py-3 pl-2 pr-6 text-right font-mono tabular-nums">{rule.hit_count}</td>
                  <td className="py-3 pl-6 pr-2 text-text-tertiary text-xs font-mono truncate">
                    {rule.project_scope === 'global' ? 'global' : rule.project_id ?? '-'}
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
    </motion.div>
  )
}
