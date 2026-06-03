import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { FilterPill } from '@/components/FilterPill'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { formatDateTime } from '@/lib/formatDateTime'
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
          <FilterPill
            key={f.value || '__all__'}
            active={levelFilter === f.value}
            label={f.label}
            onClick={() => setLevelFilter(f.value)}
          />
        ))}
      </div>

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wider text-text-tertiary border-b border-[var(--color-border-subtle)]">
                <th className="text-left py-3 px-2 w-[5rem]">{t('injections.col.rule')}</th>
                <th className="text-left py-3 px-2 w-[5rem]">{t('injections.col.level')}</th>
                <th className="text-left py-3 px-2 w-[7rem]">{t('rules.col.scope')}</th>
                <th className="text-left py-3 px-2 min-w-0">{t('injections.col.session')}</th>
                <th className="text-left py-3 px-2 w-[11rem] whitespace-nowrap">{t('injections.col.time')}</th>
              </tr>
            </thead>
            <tbody>
              {injections.map((inj) => {
                const ruleId = inj.rule_short_id ?? inj.rule_id.slice(0, 8)
                return (
                  <tr key={inj.id} className="border-b border-[var(--color-border-subtle)] hover:bg-[var(--color-row-hover)]">
                    <td className="py-3 px-2 font-mono">
                      {inj.rule_short_id ? (
                        <Link
                          to={`/rules/${inj.rule_short_id}`}
                          className="text-accent-sky hover:underline"
                        >
                          {inj.rule_short_id}
                        </Link>
                      ) : (
                        <span className="text-text-tertiary">{ruleId}</span>
                      )}
                    </td>
                    <td className="py-3 px-2"><StatusBadge status={inj.level} /></td>
                    <td className="py-3 px-2 text-text-tertiary text-xs font-mono truncate">
                      {inj.rule_project_scope === 'global' ? 'global' : inj.rule_project_id ?? '-'}
                    </td>
                    <td className="py-3 px-2 text-text-tertiary font-mono text-xs break-all">
                      {inj.session_id}
                    </td>
                    <td className="py-3 px-2 text-text-tertiary text-xs font-mono whitespace-nowrap">
                      {formatDateTime(inj.created_at)}
                    </td>
                  </tr>
                )
              })}
              {injections.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-text-tertiary">{t('injections.no_results')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </motion.div>
  )
}
