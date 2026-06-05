import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { t } from '@/lib/i18n'

export function Logs() {
  const [lines, setLines] = useState<string[]>([])
  const [level, setLevel] = useState('all')
  const [paused, setPaused] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/logs`)
    wsRef.current = ws

    ws.onopen = () => { ws.send(JSON.stringify({ level })) }
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'log') {
        setLines((prev) => [...prev.slice(-500), msg.line])
      }
    }
    return () => { ws.close() }
  }, [level])

  useEffect(() => {
    if (!paused && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [lines, paused])

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      className="space-y-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">{t('logs.title')}</h2>
        <div className="flex items-center gap-3">
          <select
            value={level}
            onChange={(e) => { setLevel(e.target.value); setLines([]) }}
            className="bg-[var(--color-input-bg)] border border-[var(--color-input-border)] rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]"
          >
            <option value="all">{t('logs.all_levels')}</option>
            <option value="debug">{t('logs.level.debug')}</option>
            <option value="info">{t('logs.level.info')}</option>
            <option value="warn">{t('logs.level.warn')}</option>
            <option value="error">{t('logs.level.error')}</option>
          </select>
          <button
            onClick={() => setPaused(!paused)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-300 ${
              paused ? 'bg-accent-amber/20 text-accent-amber' : 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]'
            }`}
          >
            {paused ? t('logs.paused') : t('logs.auto_scroll')}
          </button>
        </div>
      </div>

      <GlassCard className="min-w-0 max-w-full overflow-hidden">
        <div
          ref={containerRef}
          className="h-[calc(100dvh-200px)] overflow-y-auto overflow-x-hidden font-mono text-xs leading-relaxed space-y-0.5"
        >
          {lines.length === 0 && <p className="text-text-tertiary py-4 text-center">{t('logs.waiting')}</p>}
          {lines.map((line, i) => (
            <div
              key={i}
              className={`py-0.5 px-2 rounded break-all whitespace-pre-wrap ${
                /\bERROR\b/.test(line.slice(0, 80))
                  ? 'text-accent-rose bg-accent-rose/5'
                  : /\bWARN(?:ING)?\b/.test(line.slice(0, 80))
                  ? 'text-accent-amber bg-accent-amber/5'
                  : 'text-text-secondary'
              }`}
            >
              {line.replace(/(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})Z/, '$1 $2')}
            </div>
          ))}
        </div>
      </GlassCard>
    </motion.div>
  )
}
