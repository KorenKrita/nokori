import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { t } from '@/lib/i18n'

interface ExtractJobs { data: { pending: { path: string }[]; done: { path: string }[] } }
interface ExtractState { data: { transcript_path: string; transcript_mtime: number; extracted_at: string; status: string; last_byte_offset: number }[] }

export function Extract() {
  const { data: jobs, isLoading: l1 } = useApi<ExtractJobs>('/extract/jobs')
  const { data: state, isLoading: l2 } = useApi<ExtractState>('/extract/state')

  if (l1 || l2) return <PageSkeleton />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6"
    >
      <h2 className="text-2xl font-semibold tracking-tight">{t('extract.title')}</h2>

      <div className="grid grid-cols-2 gap-4">
        <GlassCard hover>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
            {t('extract.pending')} ({jobs?.data.pending.length ?? 0})
          </h3>
          {jobs?.data.pending.length === 0 && <p className="text-sm text-text-tertiary">{t('extract.no_pending')}</p>}
          {jobs?.data.pending.map((j, i) => (
            <p key={i} className="text-xs font-mono text-text-secondary truncate">{j.path}</p>
          ))}
        </GlassCard>

        <GlassCard hover>
          <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
            {t('extract.done')} ({jobs?.data.done.length ?? 0})
          </h3>
          {jobs?.data.done.length === 0 && <p className="text-sm text-text-tertiary">{t('extract.no_done')}</p>}
          {jobs?.data.done.slice(0, 10).map((j, i) => (
            <p key={i} className="text-xs font-mono text-text-secondary truncate">{j.path}</p>
          ))}
        </GlassCard>
      </div>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">{t('extract.state')}</h3>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-tertiary border-b border-white/[0.04]">
              <th className="text-left py-2">{t('extract.col.transcript')}</th>
              <th className="text-left py-2">{t('extract.col.status')}</th>
              <th className="text-right py-2">{t('extract.col.offset')}</th>
              <th className="text-left py-2">{t('extract.col.extracted')}</th>
            </tr>
          </thead>
          <tbody>
            {(state?.data ?? []).map((s, i) => (
              <tr key={i} className="border-b border-white/[0.03]">
                <td className="py-2 font-mono text-text-secondary truncate max-w-[300px]">{s.transcript_path}</td>
                <td className="py-2 text-text-secondary">{s.status}</td>
                <td className="py-2 text-right font-mono">{s.last_byte_offset}</td>
                <td className="py-2 text-text-tertiary">{s.extracted_at}</td>
              </tr>
            ))}
            {(state?.data ?? []).length === 0 && (
              <tr><td colSpan={4} className="py-4 text-center text-text-tertiary">{t('extract.no_state')}</td></tr>
            )}
          </tbody>
        </table>
      </GlassCard>
    </motion.div>
  )
}
