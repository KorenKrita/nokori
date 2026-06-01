import { useState } from 'react'
import { motion } from 'motion/react'
import { StatusDot } from '@/components/StatusDot'
import { mutateApi } from '@/lib/api'
import { t } from '@/lib/i18n'

interface EmbedControlProps {
  running: boolean
  pid: number | null
  onAction: () => void
}

export function EmbedControl({ running, pid, onAction }: EmbedControlProps) {
  const [loading, setLoading] = useState(false)

  const handleToggle = async () => {
    setLoading(true)
    try {
      if (running) {
        await mutateApi('/embed/stop', 'POST')
      } else {
        await mutateApi('/embed/start', 'POST')
      }
      onAction()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <StatusDot running={running} />
        <span className="text-sm">
          {running ? t('dashboard.running') : t('dashboard.stopped')}
        </span>
        {pid && <span className="text-xs text-text-tertiary font-mono">PID {pid}</span>}
      </div>
      <motion.button
        onClick={handleToggle}
        disabled={loading}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        transition={{ duration: 0.2, ease: [0.32, 0.72, 0, 1] }}
        className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors duration-300 disabled:opacity-40 ${
          running
            ? 'bg-rose-500/10 text-rose-300 hover:bg-rose-500/20'
            : 'bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20'
        }`}
      >
        {loading
          ? (running ? t('embed.stopping') : t('embed.starting'))
          : (running ? t('embed.stop') : t('embed.start'))
        }
      </motion.button>
    </div>
  )
}
