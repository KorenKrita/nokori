import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence, type Variants } from 'motion/react'
import { CaretDownIcon } from '@phosphor-icons/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { formatDateTime } from '@/lib/formatDateTime'
import { t, lz } from '@/lib/i18n'

interface ExtractJobs { data: { pending: { path: string }[]; done: { path: string; extracted_at?: string }[] } }

interface RuleReview {
  role: string
  decision: string
  scores?: Record<string, number>
  created_at: string
}

interface RuleLineage {
  old_rule_id: string
  new_rule_id: string
  operation: string
  reason: string
  created_at: string
}

interface ExtractRule {
  id: string
  short_id: string
  status: string
  trigger_canonical: string
  trigger_canonical_zh: string | null
  action_instruction: string
  action_instruction_zh: string | null
  severity: string
  source_origin: string
  created_at: string
  updated_at: string
  reviews: RuleReview[]
  lineage: RuleLineage[]
}

interface PipelineEvent {
  id: string
  source: string
  outcome: string
  details: Record<string, unknown>
  created_at: string
}

interface ExtractStateItem {
  transcript_path: string
  transcript_mtime: number
  extracted_at: string
  status: string
  last_byte_offset: number
  rules: ExtractRule[]
  pipeline_events: PipelineEvent[]
}

interface ExtractState { data: ExtractStateItem[] }

function splitPath(fullPath: string) {
  const parts = fullPath.split('/')
  const file = parts.pop() || ''
  const dir = parts.join('/')
  return { dir, file }
}

const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.05 } } }
const item: Variants = {
  hidden: { opacity: 0, y: 16, filter: 'blur(6px)' },
  show: { opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.5, ease: [0.32, 0.72, 0, 1] as const } },
}

export function Extract() {
  const { data: jobs, isLoading: l1 } = useApi<ExtractJobs>('/extract/jobs')
  const { data: state, isLoading: l2 } = useApi<ExtractState>('/extract/state')

  if (l1 || l2) return <PageSkeleton />

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="space-y-6">
      <motion.h2 variants={item} className="text-2xl font-semibold tracking-tight">
        {t('extract.title')}
      </motion.h2>

      <motion.div variants={item} className="grid grid-cols-2 gap-4">
        <GlassCard hover>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
            {t('extract.pending')} ({jobs?.data.pending.length ?? 0})
          </h3>
          {jobs?.data.pending.length === 0 && <p className="text-sm text-text-tertiary">{t('extract.no_pending')}</p>}
          <div className="space-y-2">
            {jobs?.data.pending.map((j, i) => {
              const { dir, file } = splitPath(j.path)
              return (
                <div key={i} className="py-1">
                  <p className="text-[10px] text-text-tertiary truncate">{dir}/</p>
                  <p className="text-xs font-mono text-text-secondary">{file}</p>
                </div>
              )
            })}
          </div>
        </GlassCard>

        <GlassCard hover>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
            {t('extract.done')} ({jobs?.data.done.length ?? 0})
          </h3>
          {jobs?.data.done.length === 0 && <p className="text-sm text-text-tertiary">{t('extract.no_done')}</p>}
          <div className="space-y-2">
            {jobs?.data.done.slice(0, 10).map((j, i) => {
              const { dir, file } = splitPath(j.path)
              return (
                <div key={i} className="py-1">
                  <p className="text-[10px] text-text-tertiary truncate">{dir}/</p>
                  <p className="text-xs font-mono text-text-secondary">{file}</p>
                  {j.extracted_at && (
                    <p className="text-[10px] text-text-muted font-mono">{formatDateTime(j.extracted_at)}</p>
                  )}
                </div>
              )
            })}
          </div>
        </GlassCard>
      </motion.div>

      <motion.div variants={item}>
        <GlassCard>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('extract.state')}</h3>
          <div className="space-y-3">
            {(state?.data ?? []).map((s) => (
              <TranscriptStateRow key={s.transcript_path} item={s} />
            ))}
            {(state?.data ?? []).length === 0 && (
              <p className="text-sm text-text-tertiary text-center py-4">{t('extract.no_state')}</p>
            )}
          </div>
        </GlassCard>
      </motion.div>
    </motion.div>
  )
}

function TranscriptStateRow({ item: s }: { item: ExtractStateItem }) {
  const [expanded, setExpanded] = useState(false)
  const { dir, file } = splitPath(s.transcript_path)
  const ruleCount = s.rules?.length ?? 0
  const eventCount = s.pipeline_events?.length ?? 0

  return (
    <div className="border-b border-[var(--color-border-subtle)] pb-3 last:border-0 last:pb-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left flex items-start justify-between gap-4"
      >
        <div className="min-w-0 flex-1">
          <p className="text-[10px] text-text-tertiary truncate">{dir}/</p>
          <p className="text-xs font-mono text-text-secondary">{file}</p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {ruleCount > 0 && (
            <span className="text-[10px] bg-accent-sky/10 text-accent-sky px-1.5 py-0.5 rounded font-mono">
              {ruleCount} {t('extract.rules_label')}
            </span>
          )}
          {eventCount > 0 && (
            <span className="text-[10px] bg-zinc-400/10 text-zinc-400 px-1.5 py-0.5 rounded font-mono">
              {eventCount} {t('extract.events_label')}
            </span>
          )}
          <div className="text-right">
            <p className="text-[10px] text-text-tertiary">{t('extract.col.status')}: {s.status}</p>
            <p className="text-[10px] font-mono text-text-tertiary">{t('extract.col.offset')}: {s.last_byte_offset}</p>
          </div>
          <CaretDownIcon
            size={14}
            className={`text-text-tertiary transition-transform ${expanded ? 'rotate-180' : ''}`}
          />
        </div>
      </button>
      <p className="text-[10px] text-text-muted mt-1 font-mono">{formatDateTime(s.extracted_at)}</p>

      <AnimatePresence>
        {expanded && (ruleCount > 0 || eventCount > 0) && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 pl-4 border-l-2 border-l-[var(--color-border-subtle)] space-y-3">
              {(s.rules ?? []).map((rule) => (
                <RuleLifecycleCard key={rule.id} rule={rule} />
              ))}
              {(s.pipeline_events?.length ?? 0) > 0 && (
                <div className="space-y-1">
                  <p className="text-[10px] uppercase tracking-wider text-text-tertiary">{t('extract.pipeline_events')}</p>
                  {(s.pipeline_events ?? []).map((ev) => (
                    <PipelineEventRow key={ev.id} event={ev} />
                  ))}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function RuleLifecycleCard({ rule }: { rule: ExtractRule }) {
  return (
    <div className="bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)] rounded p-3 space-y-2">
      <div className="flex items-center gap-2">
        <Link to={`/rules/${rule.short_id}`} className="font-mono text-xs text-accent-sky hover:underline">
          {rule.short_id}
        </Link>
        <StatusBadge status={rule.status} />
        <span className="text-[10px] text-text-tertiary">{rule.severity}</span>
      </div>
      <p className="text-xs text-[var(--color-text-primary)]">
        {lz(rule.trigger_canonical, rule.trigger_canonical_zh)}
      </p>
      <p className="text-[10px] text-text-secondary">
        {lz(rule.action_instruction, rule.action_instruction_zh)}
      </p>

      {(rule.reviews?.length ?? 0) > 0 && (
        <div className="space-y-1 pt-1 border-t border-[var(--color-border-subtle)]">
          {rule.reviews.map((rv, i) => (
            <div key={i} className="flex items-center gap-2 text-[10px]">
              <span className="text-text-tertiary font-mono">{rv.role}</span>
              <DecisionBadge decision={rv.decision} />
              {rv.scores?.overall_quality != null && (
                <span className="text-text-tertiary font-mono">
                  Q:{rv.scores.overall_quality.toFixed(1)}
                </span>
              )}
              <span className="text-text-muted font-mono ml-auto">{formatDateTime(rv.created_at)}</span>
            </div>
          ))}
        </div>
      )}

      {(rule.lineage?.length ?? 0) > 0 && (
        <div className="space-y-1 pt-1 border-t border-[var(--color-border-subtle)]">
          {rule.lineage.map((ln, i) => (
            <div key={i} className="flex items-center gap-2 text-[10px]">
              <span className="text-accent-violet font-mono">{ln.operation}</span>
              <span className="text-text-tertiary truncate">{ln.reason}</span>
              <span className="text-text-muted font-mono ml-auto">{formatDateTime(ln.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DecisionBadge({ decision }: { decision: string }) {
  let cls = 'px-1.5 py-0.5 rounded font-mono text-[10px] '
  if (decision === 'candidate' || decision === 'accept_candidate') {
    cls += 'bg-accent-amber/10 text-accent-amber'
  } else if (decision === 'active' || decision === 'accept_active') {
    cls += 'bg-emerald-400/10 text-emerald-400'
  } else if (decision === 'rejected' || decision === 'reject') {
    cls += 'bg-rose-400/10 text-rose-400'
  } else if (decision === 'revise') {
    cls += 'bg-accent-sky/10 text-accent-sky'
  } else {
    cls += 'bg-zinc-400/10 text-zinc-500'
  }
  return <span className={cls}>{decision}</span>
}

function PipelineEventRow({ event }: { event: PipelineEvent }) {
  let cls = 'text-[10px] px-1.5 py-0.5 rounded font-mono '
  if (event.outcome === 'candidate' || event.outcome === 'active') {
    cls += 'bg-emerald-400/10 text-emerald-400'
  } else if (event.outcome === 'rejected') {
    cls += 'bg-rose-400/10 text-rose-400'
  } else {
    cls += 'bg-zinc-400/10 text-zinc-500'
  }

  const details = event.details || {}
  const triggerEn = typeof details.trigger_preview === 'string' ? details.trigger_preview : ''
  const triggerZh = typeof details.trigger_preview_zh === 'string' ? details.trigger_preview_zh : ''
  const trigger = lz(triggerEn, triggerZh || null)
  const reason = typeof details.rejection_reason === 'string' ? details.rejection_reason : ''

  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className={cls}>{event.outcome}</span>
      {trigger && <span className="text-text-secondary truncate max-w-[20rem]">{trigger}</span>}
      {reason && <span className="text-text-tertiary truncate">{reason}</span>}
      <span className="text-text-muted font-mono ml-auto shrink-0">{formatDateTime(event.created_at)}</span>
    </div>
  )
}
