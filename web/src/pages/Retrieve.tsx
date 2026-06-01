import { useState } from 'react'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { mutateApi } from '@/lib/api'
import { t } from '@/lib/i18n'
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

  const handleSubmit = async (e: React.FormEvent) => {
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
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
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
          <div key={sr.rule.id} className="border-b border-white/[0.03] pb-3 last:border-0 last:pb-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-accent-sky">{sr.rule.short_id}</span>
              <StatusBadge status={sr.rule.status} />
            </div>
            <p className="text-sm text-text-secondary mt-1">{sr.rule.trigger_text}</p>
            <div className="flex gap-4 mt-2 text-xs text-text-tertiary font-mono">
              <span>BM25: {sr.bm25_score.toFixed(2)}</span>
              {sr.cosine !== null && <span>Cosine: {sr.cosine.toFixed(3)}</span>}
              <span>RRF: {sr.rrf_score.toFixed(4)}</span>
              {sr.matched_tokens.length > 0 && (
                <span>Tokens: {sr.matched_tokens.join(', ')}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  )
}
