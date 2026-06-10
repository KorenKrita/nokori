import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { mutateApi } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDateTime'
import { t } from '@/lib/i18n'
import {
  ruleAction,
  ruleActionZh,
  ruleSource,
  ruleTrigger,
  ruleTriggerZh,
  triggerVariantText,
} from '@/lib/ruleDisplay'
import type { Concept, ConceptGroup, ExcludedContext, Rule } from '@/lib/types'

const DATE_META_KEYS = new Set(['created_at', 'updated_at'])

function formatMetaValue(field: string, value: string): string {
  if (DATE_META_KEYS.has(field)) return formatDateTime(value) || value
  return value
}

const FIRE_LEVEL_CLASSES: Record<string, string> = {
  hot: 'bg-accent-rose/15 text-accent-rose',
  warm: 'bg-accent-amber/15 text-accent-amber',
};
const DEFAULT_FIRE_LEVEL_CLASS = 'bg-accent-violet/15 text-accent-violet';

const POSTHOC_LABEL_CLASSES: Record<string, string> = {
  observed_useful: 'bg-accent-emerald/15 text-accent-emerald',
  plausible_useful: 'bg-accent-sky/15 text-accent-sky',
  irrelevant: 'bg-[var(--color-bg-elevated)] text-text-tertiary',
  harmful: 'bg-accent-rose/15 text-accent-rose',
};
const DEFAULT_POSTHOC_CLASS = 'bg-accent-amber/15 text-accent-amber';

export function RuleDetail() {
  const { shortId } = useParams<{ shortId: string }>()
  const navigate = useNavigate()
  const { data, isLoading, refetch } = useApi<{ data: Rule }>(`/rules/${shortId}`)

  if (isLoading || !data) return <PageSkeleton />
  const rule = data.data
  const triggerVariants = rule.trigger_variants ?? []
  const triggerVariantsZh = rule.trigger_variants_zh ?? []
  const searchTerms = rule.search_terms ?? {}
  const evidenceQuotes = rule.evidence_quotes ?? []
  const concepts = (rule.concepts ?? []) as Concept[]
  const conceptGroups = (rule.required_concept_groups ?? []) as ConceptGroup[]
  const excludedContexts = (rule.excluded_contexts ?? []) as ExcludedContext[]
  const nearMiss = rule.near_miss_examples ?? []
  const allowedBehavior = rule.allowed_behavior ?? []
  const forbiddenBehavior = rule.forbidden_behavior ?? []
  const domainTags = rule.domain_tags ?? []
  const toolTags = rule.tool_tags ?? []
  const pathPatterns = rule.path_patterns ?? []

  const handleDismiss = async () => {
    await mutateApi(`/rules/${shortId}/dismiss`, 'POST')
    refetch()
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
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
          <div className="space-y-1">
            <p className="text-sm text-[var(--color-text-primary)]">
              <span className="text-[10px] text-text-tertiary mr-1.5">EN</span>{ruleTrigger(rule)}
            </p>
            {ruleTriggerZh(rule) && (
              <p className="text-sm text-text-secondary">
                <span className="text-[10px] text-text-tertiary mr-1.5">ZH</span>{ruleTriggerZh(rule)}
              </p>
            )}
          </div>
          {(triggerVariants.length > 0 || triggerVariantsZh.length > 0) && (
            <div className="mt-3 space-y-1">
              <p className="text-xs text-text-tertiary">{t('rules.variants')}:</p>
              {triggerVariants.map((v, i) => (
                <p key={i} className="text-xs text-text-secondary font-mono pl-2">{triggerVariantText(v)}</p>
              ))}
              {triggerVariantsZh.length > 0 && triggerVariantsZh.map((v, i) => (
                <p key={`zh-${i}`} className="text-xs text-text-secondary font-mono pl-2">
                  <span className="text-[10px] text-text-tertiary mr-1">zh</span>{v}
                </p>
              ))}
            </div>
          )}

          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mt-6 mb-3">{t('rules.action')}</h3>
          <div className="space-y-1">
            <p className="text-sm text-[var(--color-text-primary)]">
              <span className="text-[10px] text-text-tertiary mr-1.5">EN</span>{ruleAction(rule)}
            </p>
            {ruleActionZh(rule) && (
              <p className="text-sm text-text-secondary">
                <span className="text-[10px] text-text-tertiary mr-1.5">ZH</span>{ruleActionZh(rule)}
              </p>
            )}
          </div>

        </GlassCard>

        <div className="col-span-4 space-y-4">
          <GlassCard>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.metadata')}</h3>
            <dl className="space-y-2 text-xs">
              {(
                [
                  ['source_origin', t('rules.source_type'), ruleSource(rule)],
                  ['severity', t('rules.severity'), rule.severity ?? '-'],
                  ['schema_version', t('rules.schema_version'), String(rule.schema_version ?? '-')],
                  ['rule_version', t('rules.rule_version'), String(rule.rule_version ?? '-')],
                  ['project_scope', t('rules.project_scope'), rule.project_scope],
                  ['project_id', t('rules.project_id'), rule.project_id ?? 'global'],
                  ['created_at', t('rules.created'), rule.created_at],
                  ['updated_at', t('rules.updated'), rule.updated_at],
                ] as const
              ).map(([field, label, value]) => (
                <div key={field} className="flex justify-between gap-3">
                  <dt className="text-text-tertiary shrink-0">{label}</dt>
                  <dd className="font-mono text-text-secondary text-right break-all">
                    {formatMetaValue(field, value)}
                  </dd>
                </div>
              ))}
            </dl>
          </GlassCard>

          {Object.keys(searchTerms).length > 0 && (
            <GlassCard>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.search_terms')}</h3>
              {Object.entries(searchTerms).map(([lang, terms]) => (
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

          <GlassCard>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.activity')}</h3>
            <dl className="space-y-2 text-xs">
              <div className="flex justify-between gap-3">
                <dt className="text-text-tertiary">{t('rules.fire_count')}</dt>
                <dd className="font-mono text-text-secondary">{rule.fire_count ?? 0}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-text-tertiary">{t('rules.last_fired')}</dt>
                <dd className="font-mono text-text-secondary">
                  {rule.fire_last_at ? formatDateTime(rule.fire_last_at) : t('rules.never')}
                </dd>
              </div>
              {rule.fire_levels && Object.keys(rule.fire_levels).length > 0 && (
                <div className="flex justify-between gap-3">
                  <dt className="text-text-tertiary">{t('rules.fire_levels')}</dt>
                  <dd className="flex flex-wrap gap-1">
                    {Object.entries(rule.fire_levels).map(([level, count]) => (
                      <span
                        key={level}
                        className={`px-2 py-0.5 rounded text-xs font-mono ${
                          FIRE_LEVEL_CLASSES[level] ?? DEFAULT_FIRE_LEVEL_CLASS
                        }`}
                      >
                        {level} {count}
                      </span>
                    ))}
                  </dd>
                </div>
              )}
              {(rule.shadow_count ?? 0) > 0 && (
                <div className="flex justify-between gap-3">
                  <dt className="text-text-tertiary">{t('rules.shadow_count')}</dt>
                  <dd className="font-mono text-text-secondary">{rule.shadow_count}</dd>
                </div>
              )}
            </dl>
          </GlassCard>

          {rule.posthoc_labels && Object.keys(rule.posthoc_labels).length > 0 && (
            <GlassCard>
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.posthoc')}</h3>
              <div className="space-y-2">
                {Object.entries(rule.posthoc_labels).map(([label, count]) => (
                  <div key={label} className="flex items-center justify-between gap-2">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-mono ${
                        POSTHOC_LABEL_CLASSES[label] ?? DEFAULT_POSTHOC_CLASS
                      }`}
                    >
                      {label}
                    </span>
                    <span className="font-mono text-xs text-text-secondary">{count}</span>
                  </div>
                ))}
              </div>
            </GlassCard>
          )}
        </div>
      </div>

      {/* Concepts & Matching Structure */}
      {(concepts.length > 0 || conceptGroups.length > 0 || excludedContexts.length > 0) && (
        <div className="grid grid-cols-12 gap-4">
          {concepts.length > 0 && (
            <GlassCard className="col-span-8">
              <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.concepts')}</h3>
              <div className="space-y-3">
                {concepts.map((c) => (
                  <div key={c.id} className="border-b border-[var(--color-border-subtle)] pb-2 last:border-0 last:pb-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-accent-sky">{c.id}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${c.required ? 'bg-accent-rose/15 text-accent-rose' : 'bg-[var(--color-bg-elevated)] text-text-tertiary'}`}>
                        {c.required ? t('rules.concept.required') : t('rules.concept.optional')}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-elevated)] text-text-tertiary font-mono">
                        {c.match_mode}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {c.aliases.map((a, i) => (
                        <span key={i} className={`px-2 py-0.5 rounded text-xs font-mono ${a.strength === 'strong' ? 'bg-accent-emerald/10 text-accent-emerald' : 'bg-[var(--color-bg-elevated)] text-text-tertiary'}`}>
                          {a.text}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </GlassCard>
          )}

          <div className="col-span-4 space-y-4">
            {conceptGroups.length > 0 && (
              <GlassCard>
                <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.concept_groups')}</h3>
                <div className="space-y-2">
                  {conceptGroups.map((g) => (
                    <div key={g.id} className="text-xs">
                      <span className="font-mono text-accent-sky">{g.id}</span>
                      <span className="text-text-tertiary"> {t('rules.concept.all_of')}: </span>
                      <span className="font-mono text-text-secondary">[{g.all_of.join(', ')}]</span>
                    </div>
                  ))}
                </div>
              </GlassCard>
            )}

            {excludedContexts.length > 0 && (
              <GlassCard>
                <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.excluded_contexts')}</h3>
                <div className="space-y-2">
                  {excludedContexts.map((ec) => (
                    <div key={ec.id} className="text-xs">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-accent-rose">{ec.id}</span>
                        {ec.scope && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-elevated)] text-text-tertiary">{ec.scope}</span>}
                        {ec.match_mode && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-elevated)] text-text-tertiary">{ec.match_mode}</span>}
                      </div>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {ec.patterns.map((p, i) => (
                          <span key={i} className="px-2 py-0.5 rounded bg-accent-rose/10 text-xs font-mono text-accent-rose/80">{p}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </GlassCard>
            )}
          </div>
        </div>
      )}

      {/* Quality & Lifecycle Scores */}
      {(rule.quality_score != null || rule.observed_usefulness_score != null) && (
        <div className="grid grid-cols-12 gap-4">
          <GlassCard className="col-span-6">
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.quality_scores')}</h3>
            <dl className="space-y-2 text-xs">
              {([
                ['quality', 'rules.score.quality', rule.quality_score],
                ['evidence_support', 'rules.score.evidence_support', rule.evidence_support_score],
                ['specificity', 'rules.score.specificity', rule.specificity_score],
                ['retrieval_readiness', 'rules.score.retrieval_readiness', rule.retrieval_readiness_score],
              ] as const).filter(([, , v]) => v != null).map(([key, i18nKey, value]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt className="text-text-tertiary">{t(i18nKey)}</dt>
                  <dd className="font-mono text-text-secondary">{(value as number).toFixed(2)}</dd>
                </div>
              ))}
            </dl>
          </GlassCard>

          <GlassCard className="col-span-6">
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.lifecycle_scores')}</h3>
            <dl className="space-y-2 text-xs">
              {([
                ['observed_useful', 'rules.score.observed_useful', rule.observed_usefulness_score],
                ['plausible_useful', 'rules.score.plausible_useful', rule.plausible_usefulness_score],
                ['false_positive', 'rules.score.false_positive', rule.false_positive_score],
                ['harmful', 'rules.score.harmful', rule.harmful_score],
              ] as const).filter(([, , v]) => v != null).map(([key, i18nKey, value]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt className="text-text-tertiary">{t(i18nKey)}</dt>
                  <dd className="font-mono text-text-secondary">{(value as number).toFixed(2)}</dd>
                </div>
              ))}
            </dl>
          </GlassCard>
        </div>
      )}

      {/* Tags, behaviors, near-miss */}
      {(domainTags.length > 0 || toolTags.length > 0 || pathPatterns.length > 0 || nearMiss.length > 0 || allowedBehavior.length > 0 || forbiddenBehavior.length > 0) && (
        <GlassCard>
          <div className="grid grid-cols-2 gap-4">
            {domainTags.length > 0 && (
              <div>
                <h4 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-2">{t('rules.domain_tags')}</h4>
                <div className="flex flex-wrap gap-1">
                  {domainTags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 rounded bg-accent-violet/10 text-xs font-mono text-accent-violet">{tag}</span>
                  ))}
                </div>
              </div>
            )}
            {toolTags.length > 0 && (
              <div>
                <h4 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-2">{t('rules.tool_tags')}</h4>
                <div className="flex flex-wrap gap-1">
                  {toolTags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 rounded bg-accent-sky/10 text-xs font-mono text-accent-sky">{tag}</span>
                  ))}
                </div>
              </div>
            )}
            {pathPatterns.length > 0 && (
              <div>
                <h4 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-2">{t('rules.path_patterns')}</h4>
                <div className="flex flex-wrap gap-1">
                  {pathPatterns.map((p) => (
                    <span key={p} className="px-2 py-0.5 rounded bg-[var(--color-bg-elevated)] text-xs font-mono text-text-secondary">{p}</span>
                  ))}
                </div>
              </div>
            )}
            {nearMiss.length > 0 && (
              <div>
                <h4 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-2">{t('rules.near_miss')}</h4>
                <div className="space-y-1">
                  {nearMiss.map((ex, i) => (
                    <p key={i} className="text-xs text-text-secondary font-mono pl-2">{ex}</p>
                  ))}
                </div>
              </div>
            )}
            {allowedBehavior.length > 0 && (
              <div>
                <h4 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-2">{t('rules.allowed_behavior')}</h4>
                <div className="space-y-1">
                  {allowedBehavior.map((b, i) => (
                    <p key={i} className="text-xs text-accent-emerald font-mono pl-2">{b}</p>
                  ))}
                </div>
              </div>
            )}
            {forbiddenBehavior.length > 0 && (
              <div>
                <h4 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-2">{t('rules.forbidden_behavior')}</h4>
                <div className="space-y-1">
                  {forbiddenBehavior.map((b, i) => (
                    <p key={i} className="text-xs text-accent-rose font-mono pl-2">{b}</p>
                  ))}
                </div>
              </div>
            )}
          </div>
        </GlassCard>
      )}

      {evidenceQuotes.length > 0 && (
        <GlassCard>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('rules.evidence_quotes')}</h3>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {evidenceQuotes.map((quote: string, i: number) => (
              <div key={i} className="text-xs font-mono text-text-secondary py-1 border-b border-[var(--color-border-subtle)]">
                &ldquo;{quote}&rdquo;
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </motion.div>
  )
}
