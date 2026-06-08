import { motion, type Variants } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { formatDateTime } from '@/lib/formatDateTime'
import { t } from '@/lib/i18n'

interface ExtractJobs { data: { pending: { path: string }[]; done: { path: string; extracted_at?: string }[] } }
interface ExtractState { data: { transcript_path: string; transcript_mtime: number; extracted_at: string; status: string; last_byte_offset: number }[] }

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
            {(state?.data ?? []).map((s, i) => {
              const { dir, file } = splitPath(s.transcript_path)
              return (
                <div key={i} className="border-b border-[var(--color-border-subtle)] pb-3 last:border-0 last:pb-0">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <p className="text-[10px] text-text-tertiary truncate">{dir}/</p>
                      <p className="text-xs font-mono text-text-secondary">{file}</p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-[10px] text-text-tertiary">{t('extract.col.status')}: {s.status}</p>
                      <p className="text-[10px] font-mono text-text-tertiary">{t('extract.col.offset')}: {s.last_byte_offset}</p>
                    </div>
                  </div>
                  <p className="text-[10px] text-text-muted mt-1 font-mono">{formatDateTime(s.extracted_at)}</p>
                </div>
              )
            })}
            {(state?.data ?? []).length === 0 && (
              <p className="text-sm text-text-tertiary text-center py-4">{t('extract.no_state')}</p>
            )}
          </div>
        </GlassCard>
      </motion.div>
    </motion.div>
  )
}
