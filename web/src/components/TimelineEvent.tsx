import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'motion/react'
import { CaretDownIcon } from '@phosphor-icons/react'
import { formatDateTime } from '@/lib/formatDateTime'
import { getSourceColor } from '@/lib/sourceColors'
import type { TimelineEvent as TEvent } from '@/lib/types'

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return null
  let cls = 'text-xs px-1.5 py-0.5 rounded font-mono '
  if (outcome === 'ok' || outcome === 'injected' || outcome === 'active' || outcome === 'added') {
    cls += 'bg-emerald-400/10 text-emerald-400'
  } else if (outcome === 'blocked') {
    cls += 'bg-rose-400/10 text-rose-400'
  } else if (outcome.startsWith('passed_') || outcome === 'deferred' || outcome === 'no_matches' || outcome === 'no_rules') {
    cls += 'bg-zinc-400/10 text-zinc-500'
  } else if (outcome.includes('failed') || outcome === 'rejected') {
    cls += 'bg-amber-400/10 text-amber-400'
  } else if (outcome === 'pending') {
    cls += 'bg-sky-400/10 text-sky-400'
  } else {
    cls += 'bg-zinc-400/10 text-zinc-500'
  }
  return <span className={cls}>{outcome}</span>
}

export function TimelineEventRow({ event }: { event: TEvent }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border-b border-[var(--color-border-subtle)] last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[var(--color-row-hover)] transition-colors text-sm"
      >
        <span className="text-xs text-text-tertiary font-mono w-[7.5rem] shrink-0">
          {formatDateTime(event.created_at)}
        </span>
        <span className={`font-medium w-[10rem] shrink-0 ${getSourceColor(event.source)}`}>
          {event.source}
        </span>
        <OutcomeBadge outcome={event.outcome} />
        {event.prompt_snippet && (
          <span className="text-text-tertiary truncate ml-2 text-xs italic">
            "{event.prompt_snippet.slice(0, 60)}{event.prompt_snippet.length > 60 ? '...' : ''}"
          </span>
        )}
        <CaretDownIcon
          size={14}
          className={`ml-auto text-text-tertiary transition-transform shrink-0 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      <AnimatePresence>
        {expanded && event.details && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 pl-[8.5rem]">
              <div className="text-xs font-mono text-text-secondary space-y-1 bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)] rounded p-3">
                {Object.entries(event.details).map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <span className="text-text-tertiary">{key}:</span>
                    <span className="text-text-secondary break-all">
                      {renderDetailValue(key, value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function renderDetailValue(key: string, value: unknown): React.ReactNode {
  if (value === null || value === undefined) return <span className="text-text-muted">null</span>
  if (typeof value === 'boolean') return value ? '✓' : '✗'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') {
    if (key.includes('rule') && /^[a-f0-9]{6,32}$/.test(value)) {
      return <Link to={`/rules/${value}`} className="text-accent-sky hover:underline">{value}</Link>
    }
    return value
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-text-muted">[]</span>
    return (
      <span className="flex flex-wrap gap-1">
        {value.map((item, i) => {
          if (typeof item === 'object' && item !== null && 'short_id' in item) {
            const sid = (item as { short_id: string }).short_id
            return <Link key={i} to={`/rules/${sid}`} className="text-accent-sky hover:underline">{sid}</Link>
          }
          return <span key={i}>{JSON.stringify(item)}</span>
        })}
      </span>
    )
  }
  return JSON.stringify(value)
}
