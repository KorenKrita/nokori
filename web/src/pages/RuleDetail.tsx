import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { mutateApi } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDateTime'
import { t } from '@/lib/i18n'
import type { Rule } from '@/lib/types'

const DATE_META_KEYS = new Set(['last_hit', 'created_at', 'updated_at'])

function formatMetaValue(field: string, value: string, neverLabel: string): string {
  if (field === 'last_hit' && value === neverLabel) return value
  if (DATE_META_KEYS.has(field)) return formatDateTime(value) || value
  return value
}

export function RuleDetail() {
  const { shortId } = useParams<{ shortId: string }>()
  const navigate = useNavigate()
  const { data, isLoading, refetch } = useApi<{ data: Rule }>(`/rules/${shortId}`)

  if (isLoading || !data) return <PageSkeleton />
  const rule = data.data

  const handleDismiss = async () => {
    await mutateApi(`/rules/${shortId}/dismiss`, 'POST')
    refetch()
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/rules')}
            className="text-text-tertiary hover:text-[var(--color-text-primary)] text-sm"
          >
            {t('rules.back')} /
          </button>
          <h2 className="text-2xl font-semibold tracking-tight font-mono">{rule.short_id}</h2>
          <StatusBadge status={rule.status} />
        </div>
        {rule.status !== 'archived' && (
          <motion.button
            onClick={handleDismiss}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            className="px-4 py-2 rounded-full bg-accent-rose/10 text-accent-rose text-sm font-medium hover:bg-accent-rose/20 transition-all duration-300"
          >
            {t('rules.dismiss')}
          </motion.button>
        )}
      </div>

      <div className="grid grid-cols-12 gap-4">
        <GlassCard className="col-span-8">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.trigger')}</h3>
          <p className="text-sm text-[var(--color-text-primary)]">{rule.trigger_text}</p>
          {rule.trigger_variants.length > 0 && (
            <div className="mt-3 space-y-1">
              <p className="text-xs text-text-tertiary">{t('rules.variants')}:</p>
              {rule.trigger_variants.map((v, i) => (
                <p key={i} className="text-xs text-text-secondary font-mono pl-2">{v}</p>
              ))}
            </div>
          )}

          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">{t('rules.action')}</h3>
          <p className="text-sm text-[var(--color-text-primary)]">{rule.action}</p>

          {rule.behavior && (
            <>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">{t('rules.behavior')}</h3>
              <p className="text-sm text-text-secondary">{rule.behavior}</p>
            </>
          )}

          {rule.rationale && (
            <>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">{t('rules.rationale')}</h3>
              <p className="text-sm text-text-secondary">{rule.rationale}</p>
            </>
          )}
        </GlassCard>

        <div className="col-span-4 space-y-4">
          <GlassCard>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.metadata')}</h3>
            <dl className="space-y-2 text-xs">
              {(
                [
                  ['source_type', t('rules.source_type'), rule.source_type],
                  ['confidence', t('rules.confidence'), rule.confidence],
                  ['evidence_score', t('rules.evidence_score'), String(rule.evidence_score)],
                  ['hit_count', t('rules.hit_count'), String(rule.hit_count)],
                  ['last_hit', t('rules.last_hit'), rule.last_hit ?? t('rules.never')],
                  ['project_scope', t('rules.project_scope'), rule.project_scope],
                  ['project_id', t('rules.project_id'), rule.project_id ?? 'global'],
                  ['created_at', t('rules.created'), rule.created_at],
                  ['updated_at', t('rules.updated'), rule.updated_at],
                ] as const
              ).map(([field, label, value]) => (
                <div key={field} className="flex justify-between gap-3">
                  <dt className="text-text-tertiary shrink-0">{label}</dt>
                  <dd className="font-mono text-text-secondary text-right break-all">
                    {formatMetaValue(field, value, t('rules.never'))}
                  </dd>
                </div>
              ))}
            </dl>
          </GlassCard>

          {Object.keys(rule.search_terms).length > 0 && (
            <GlassCard>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.search_terms')}</h3>
              {Object.entries(rule.search_terms).map(([lang, terms]) => (
                <div key={lang} className="mb-2">
                  <span className="text-xs text-text-tertiary">{lang}:</span>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {terms.map((term) => (
                      <span key={term} className="px-2 py-0.5 rounded bg-[var(--color-bg-elevated)] text-xs font-mono text-text-secondary">
                        {term}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </GlassCard>
          )}
        </div>
      </div>

      {rule.evidence_log.length > 0 && (
        <GlassCard>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.evidence_log')}</h3>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {rule.evidence_log.map((entry, i) => (
              <div key={i} className="text-xs font-mono text-text-secondary py-1 border-b border-[var(--color-border-subtle)]">
                {JSON.stringify(entry)}
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </motion.div>
  )
}
