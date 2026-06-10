import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { mutateApi } from '@/lib/api'
import { t } from '@/lib/i18n'
import { ruleTrigger, ruleTriggerZh } from '@/lib/ruleDisplay'
import type { ScoredResult } from '@/lib/types'

interface RetrieveResponse {
  data: {
    hot: ScoredResult[]
    warm: ScoredResult[]
    shadow_hot: ScoredResult[]
    shadow_warm: ScoredResult[]
    embed_mode: string
    bm25_matches: number
  }
}

export function Retrieve() {
  const [prompt, setPrompt] = useState('')
  const [useEmbed, setUseEmbed] = useState(true)
  const [result, setResult] = useState<RetrieveResponse['data'] | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.SubmitEvent) => {
    e.preventDefault()
    if (!prompt.trim()) return
    setLoading(true)
    try {
      const res = await mutateApi<RetrieveResponse>('/retrieve', 'POST', {
        prompt,
        use_embedding: useEmbed,
      })
      setResult(res.data)
    } finally {
      setLoading(false)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      className="space-y-6"
    >
      <h2 className="text-2xl font-semibold tracking-tight">{t('retrieve.title')}</h2>

      <GlassCard>
        <form onSubmit={handleSubmit} className="space-y-4">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t('retrieve.placeholder')}
            className="w-full h-28 bg-[var(--color-input-bg)] border border-[var(--color-input-border)] rounded-lg px-4 py-3 text-sm text-[var(--color-text-primary)] placeholder:text-text-tertiary focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)] resize-none"
          />
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={useEmbed}
                onChange={(e) => setUseEmbed(e.target.checked)}
                className="rounded"
              />
              {t('retrieve.use_embedding')}
            </label>
            <motion.button
              type="submit"
              disabled={loading || !prompt.trim()}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              className="px-4 py-2 rounded-full bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)] text-sm font-medium hover:bg-[var(--color-bg-elevated)] disabled:opacity-40 transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
            >
              {loading ? t('retrieve.searching') : t('retrieve.search')}
            </motion.button>
          </div>
        </form>
      </GlassCard>

      {result && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="space-y-4"
        >
          <div className="flex gap-4 text-xs text-text-tertiary">
            <span>{t('retrieve.mode')}: <span className="font-mono text-text-secondary">{result.embed_mode}</span></span>
            <span>{t('retrieve.bm25_matches')}: <span className="font-mono text-text-secondary">{result.bm25_matches}</span></span>
          </div>

          <ResultSection title="HOT" items={result.hot} level="hot" />
          <ResultSection title="WARM" items={result.warm} level="warm" />

          {(result.shadow_hot.length > 0 || result.shadow_warm.length > 0) && (
            <details className="mt-4">
              <summary className="text-xs text-text-tertiary cursor-pointer hover:text-text-secondary">
                {t('retrieve.shadow_pool')} ({result.shadow_hot.length + result.shadow_warm.length} {t('retrieve.results', { n: result.shadow_hot.length + result.shadow_warm.length })})
              </summary>
              <div className="mt-2 space-y-4">
                <ResultSection title="Shadow HOT" items={result.shadow_hot} level="hot" />
                <ResultSection title="Shadow WARM" items={result.shadow_warm} level="warm" />
              </div>
            </details>
          )}
        </motion.div>
      )}
    </motion.div>
  )
}

function BoolDot({ value, label }: { value: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${value ? 'bg-accent-emerald' : 'bg-text-tertiary/30'}`} />
      {label}
    </span>
  )
}

function ResultSection({ title, items, level }: { title: string; items: ScoredResult[]; level: string }) {
  if (items.length === 0) return null
  return (
    <GlassCard>
      <div className="flex items-center gap-2 mb-3">
        <StatusBadge status={level} />
        <span className="text-xs text-text-tertiary">{title} ({items.length})</span>
      </div>
      <div className="space-y-3">
        {items.map((sr) => (
          <div key={sr.rule.id} className="border-b border-[var(--color-border-subtle)] pb-3 last:border-0 last:pb-0">
            <div className="flex items-center gap-2">
              <Link to={`/rules/${sr.rule.short_id}`} className="font-mono text-xs text-accent-sky hover:underline">
                {sr.rule.short_id}
              </Link>
              <StatusBadge status={sr.rule.status} />
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                sr.eligibility.decision === 'hot' ? 'bg-accent-rose/15 text-accent-rose' :
                sr.eligibility.decision === 'warm' ? 'bg-accent-amber/15 text-accent-amber' :
                'bg-[var(--color-bg-elevated)] text-text-tertiary'
              }`}>
                {sr.eligibility.decision}
              </span>
            </div>
            <p className="text-sm text-text-secondary mt-1">{ruleTrigger(sr.rule)}</p>
            {ruleTriggerZh(sr.rule) && (
              <p className="text-xs text-text-tertiary mt-0.5">{ruleTriggerZh(sr.rule)}</p>
            )}

            {/* Scores row */}
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-text-tertiary font-mono">
              <span>BM25: {sr.bm25_score.toFixed(2)}</span>
              {sr.cosine !== null && <span>Cosine: {sr.cosine.toFixed(3)}</span>}
              <span>RRF: {sr.rrf_score.toFixed(4)}</span>
              <span>Utility: {sr.ranking_utility.toFixed(3)}</span>
            </div>

            {/* Evidence row */}
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5 text-xs text-text-tertiary font-mono">
              <span>Coverage: {sr.decision_features.trigger_coverage.toFixed(2)}</span>
              <span>IDF: {sr.decision_features.trigger_idf_sum.toFixed(2)}</span>
              <span>Terms: {sr.decision_features.distinct_trigger_terms}</span>
              <BoolDot value={sr.eligibility.trigger_evidence_passed} label="evidence" />
              <BoolDot value={sr.decision_features.strong_variant_phrase_hit} label="strong_var" />
              <BoolDot value={sr.decision_features.weak_variant_recall_hit} label="weak_var" />
              <BoolDot value={sr.decision_features.required_concepts_match} label="concepts" />
            </div>

            {/* Flags row */}
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1 text-xs text-text-tertiary font-mono">
              {sr.decision_features.excluded_context_hit && <BoolDot value={true} label="excluded_ctx" />}
              {sr.decision_features.excluded_context_override_passed && <BoolDot value={true} label="ctx_override" />}
              {sr.decision_features.action_only_match && <BoolDot value={true} label="action_only" />}
              {sr.decision_features.search_only_match && <BoolDot value={true} label="search_only" />}
              {sr.decision_features.embedding_only_match && <BoolDot value={true} label="embed_only" />}
              {sr.decision_features.embedding_cosine !== undefined && (
                <span>embed_cos: {sr.decision_features.embedding_cosine.toFixed(3)}</span>
              )}
              {sr.decision_features.embedding_profile_bucket && (
                <span>bucket: {sr.decision_features.embedding_profile_bucket}</span>
              )}
            </div>

            {/* Matched tokens */}
            <div className="mt-1.5 text-xs text-text-tertiary font-mono space-y-0.5">
              {sr.decision_features.matched_trigger_tokens.length > 0 && (
                <p>trigger: {sr.decision_features.matched_trigger_tokens.join(', ')}</p>
              )}
              {sr.decision_features.matched_variant_tokens.length > 0 && (
                <p>variant: {sr.decision_features.matched_variant_tokens.join(', ')}</p>
              )}
              {sr.decision_features.matched_search_tokens && sr.decision_features.matched_search_tokens.length > 0 && (
                <p>search: {sr.decision_features.matched_search_tokens.join(', ')}</p>
              )}
              {sr.decision_features.matched_action_tokens && sr.decision_features.matched_action_tokens.length > 0 && (
                <p>action: {sr.decision_features.matched_action_tokens.join(', ')}</p>
              )}
            </div>

            {/* Decision reason & penalties */}
            {sr.decision_reason && (
              <p className="mt-1.5 text-[11px] text-text-tertiary italic">{sr.decision_reason}</p>
            )}
            {sr.eligibility.penalties.length > 0 && (
              <p className="mt-0.5 text-[11px] text-accent-rose/80 font-mono">penalties: {sr.eligibility.penalties.join(', ')}</p>
            )}
          </div>
        ))}
      </div>
    </GlassCard>
  )
}
