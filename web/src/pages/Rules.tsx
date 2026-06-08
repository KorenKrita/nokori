import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { FilterPill } from '@/components/FilterPill'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { t, lz } from '@/lib/i18n'
import { ruleSource, ruleTrigger, ruleTriggerZh } from '@/lib/ruleDisplay'
import type { Meta, Rule } from '@/lib/types'

const STATUS_FILTERS = [
  { value: 'active,trusted', labelKey: 'rules.filter.formal', helpKey: 'rules.filter.help.formal' },
  { value: 'active', labelKey: 'rules.filter.active', helpKey: 'rules.filter.help.active' },
  { value: 'trusted', labelKey: 'rules.filter.trusted', helpKey: 'rules.filter.help.trusted' },
  { value: 'candidate', labelKey: 'rules.filter.candidate', helpKey: 'rules.filter.help.candidate' },
  { value: 'suppressed', labelKey: 'rules.filter.suppressed', helpKey: 'rules.filter.help.suppressed' },
  { value: 'archived', labelKey: 'rules.filter.archived', helpKey: 'rules.filter.help.archived' },
  { value: '', labelKey: 'rules.filter.all', helpKey: 'rules.filter.help.all' },
] as const

export function Rules() {
  const [statusFilter, setStatusFilter] = useState('active,trusted')
  const [scopeFilter, setScopeFilter] = useState('')
  const params: Record<string, string> = { status: statusFilter }
  if (scopeFilter === 'global') {
    params.scope = 'global'
  } else if (scopeFilter) {
    params.project = scopeFilter
  }
  const { data, isLoading } = useApi<{ data: Rule[]; meta: Meta }>(
    '/rules',
    params
  )

  const allRulesData = useApi<{ data: Rule[]; meta: Meta }>('/rules', { status: '' })
  const projectIds = useMemo(() => {
    const ids = new Set<string>()
    for (const rule of allRulesData.data?.data ?? []) {
      if (rule.project_id) ids.add(rule.project_id)
    }
    return Array.from(ids).sort()
  }, [allRulesData.data])

  if (isLoading) return <PageSkeleton />

  const rules = data?.data ?? []
  const meta = data?.meta

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
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

      <div className="flex gap-2 overflow-x-auto scrollbar-none scroll-fade-x pb-1">
        <FilterPill
          active={scopeFilter === ''}
          label={t('rules.filter.all')}
          onClick={() => setScopeFilter('')}
        />
        <FilterPill
          active={scopeFilter === 'global'}
          label={t('rules.scope.global')}
          onClick={() => setScopeFilter('global')}
        />
        {projectIds.map((pid) => (
          <FilterPill
            key={pid}
            active={scopeFilter === pid}
            label={pid}
            onClick={() => setScopeFilter(pid)}
          />
        ))}
      </div>

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-60px' }}
        transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      >
      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm table-fixed">
            <colgroup>
              <col className="w-[4.5rem]" />
              <col className="w-[5.5rem]" />
              <col className="w-[8.5rem]" />
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
                <th className="text-right py-3 pl-2 pr-6">{t('rules.col.fires')}</th>
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
                  <td className="py-3 px-2 text-text-secondary truncate">{ruleSource(rule)}</td>
                  <td className="py-3 px-2 text-text-secondary truncate">{lz(ruleTrigger(rule), ruleTriggerZh(rule))}</td>
                  <td className="py-3 pl-2 pr-6 text-right font-mono tabular-nums text-text-tertiary">{rule.fire_count ?? 0}</td>
                  <td className="py-3 pl-6 pr-2 text-text-tertiary text-xs font-mono truncate">
                    {rule.project_scope === 'global' ? t('rules.scope.global') : rule.project_id ?? '-'}
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
