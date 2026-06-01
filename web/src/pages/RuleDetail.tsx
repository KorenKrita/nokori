import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { mutateApi } from '@/lib/api'
import type { Rule } from '@/lib/types'

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
            className="text-text-tertiary hover:text-white text-sm"
          >
            Rules /
          </button>
          <h2 className="text-2xl font-semibold tracking-tight font-mono">{rule.short_id}</h2>
          <StatusBadge status={rule.status} />
        </div>
        {rule.status !== 'archived' && (
          <button
            onClick={handleDismiss}
            className="px-4 py-2 rounded-full bg-rose-500/10 text-rose-300 text-sm font-medium hover:bg-rose-500/20 transition-all duration-300 active:scale-[0.98]"
          >
            Dismiss
          </button>
        )}
      </div>

      <div className="grid grid-cols-12 gap-4">
        <GlassCard className="col-span-8">
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Trigger</h3>
          <p className="text-sm text-white">{rule.trigger_text}</p>
          {rule.trigger_variants.length > 0 && (
            <div className="mt-3 space-y-1">
              <p className="text-xs text-text-tertiary">Variants:</p>
              {rule.trigger_variants.map((v, i) => (
                <p key={i} className="text-xs text-text-secondary font-mono pl-2">{v}</p>
              ))}
            </div>
          )}

          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">Action</h3>
          <p className="text-sm text-white">{rule.action}</p>

          {rule.behavior && (
            <>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">Behavior (incorrect)</h3>
              <p className="text-sm text-text-secondary">{rule.behavior}</p>
            </>
          )}

          {rule.rationale && (
            <>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">Rationale</h3>
              <p className="text-sm text-text-secondary">{rule.rationale}</p>
            </>
          )}
        </GlassCard>

        <div className="col-span-4 space-y-4">
          <GlassCard>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Metadata</h3>
            <dl className="space-y-2 text-xs">
              {([
                ['Source type', rule.source_type],
                ['Confidence', rule.confidence],
                ['Evidence score', String(rule.evidence_score)],
                ['Hit count', String(rule.hit_count)],
                ['Last hit', rule.last_hit ?? 'never'],
                ['Project scope', rule.project_scope],
                ['Project ID', rule.project_id ?? 'global'],
                ['Created', rule.created_at],
                ['Updated', rule.updated_at],
              ] as [string, string][]).map(([label, value]) => (
                <div key={label} className="flex justify-between">
                  <dt className="text-text-tertiary">{label}</dt>
                  <dd className="font-mono text-text-secondary">{value}</dd>
                </div>
              ))}
            </dl>
          </GlassCard>

          {Object.keys(rule.search_terms).length > 0 && (
            <GlassCard>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Search Terms</h3>
              {Object.entries(rule.search_terms).map(([lang, terms]) => (
                <div key={lang} className="mb-2">
                  <span className="text-xs text-text-tertiary">{lang}:</span>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {terms.map((t) => (
                      <span key={t} className="px-2 py-0.5 rounded bg-white/[0.04] text-xs font-mono text-text-secondary">
                        {t}
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
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">Evidence Log</h3>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {rule.evidence_log.map((entry, i) => (
              <div key={i} className="text-xs font-mono text-text-secondary py-1 border-b border-white/[0.02]">
                {JSON.stringify(entry)}
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </motion.div>
  )
}
