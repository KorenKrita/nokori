import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { EmptyState } from '@/components/EmptyState'
import { t } from '@/lib/i18n'

type LogLevel = 'error' | 'warn' | 'info' | 'debug' | null

function parseLevel(line: string): LogLevel {
  const prefix = line.slice(0, 80)
  if (/\bERROR\b/.test(prefix)) return 'error'
  if (/\bWARN(?:ING)?\b/.test(prefix)) return 'warn'
  if (/\bINFO\b/.test(prefix)) return 'info'
  if (/\bDEBUG\b/.test(prefix)) return 'debug'
  return null
}

function getLineColorClass(level: LogLevel): string {
  if (level === 'error') return 'text-accent-rose bg-accent-rose/5'
  if (level === 'warn') return 'text-accent-amber bg-accent-amber/5'
  return 'text-text-secondary'
}

const levelBadgeClass: Record<NonNullable<LogLevel>, string> = {
  error: 'bg-accent-rose/15 text-accent-rose',
  warn: 'bg-accent-amber/15 text-accent-amber',
  info: 'bg-accent-sky/15 text-accent-sky',
  debug: 'bg-[var(--color-bg-elevated)] text-text-tertiary',
}

interface LogEntry { id: number; line: string }

export function Logs() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [level, setLevel] = useState('all')
  const [paused, setPaused] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const idRef = useRef(0)
  const rafRef = useRef(0)

  useEffect(() => {
    setEntries([])
    idRef.current = 0
    let stale = false
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/logs`)
    wsRef.current = ws

    ws.onopen = () => { ws.send(JSON.stringify({ level })) }
    ws.onmessage = (event) => {
      if (stale) return
      let msg
      try { msg = JSON.parse(event.data) } catch (e) {
        if (import.meta.env.DEV) console.warn('Failed to parse WS message:', e)
        return
      }
      if (msg.type === 'log') {
        const id = ++idRef.current
        setEntries((prev) => [...prev.slice(-500), { id, line: msg.line }])
      }
    }
    return () => { stale = true; ws.close() }
  }, [level])

  useEffect(() => {
    if (paused || !containerRef.current) return () => {}
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      if (containerRef.current) {
        containerRef.current.scrollTop = containerRef.current.scrollHeight
      }
    })
    return () => { cancelAnimationFrame(rafRef.current) }
  }, [entries, paused])

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
            onChange={(e) => { setLevel(e.target.value) }}
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
          {entries.length === 0 && <EmptyState message={t('logs.waiting')} />}
          {entries.map((entry) => {
            const logLevel = parseLevel(entry.line)
            return (
              <div
                key={entry.id}
                className={`flex items-start rounded ${getLineColorClass(logLevel)}`}
              >
                <span className="shrink-0 min-w-10 text-right pr-3 py-0.5 text-text-muted select-none border-r border-[var(--color-border-subtle)]">
                  {entry.id}
                </span>
                {logLevel ? (
                  <span className={`shrink-0 w-12 ml-2 mt-0.5 px-1.5 py-px rounded text-[10px] font-medium uppercase text-center ${levelBadgeClass[logLevel]}`}>
                    {logLevel.toUpperCase()}
                  </span>
                ) : (
                  <span className="shrink-0 w-12 ml-2" />
                )}
                <span className="py-0.5 px-2 break-all whitespace-pre-wrap">
                  {entry.line.replace(/(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})Z/, '$1 $2')}
                </span>
              </div>
            )
          })}
        </div>
      </GlassCard>
    </motion.div>
  )
}
