import { useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { CaretDownIcon } from '@phosphor-icons/react'
import { formatDateTime } from '@/lib/formatDateTime'
import { getSourceColor } from '@/lib/sourceColors'
import { TimelineEventRow } from './TimelineEvent'
import type { TimelineEvent } from '@/lib/types'

export interface EventGroup {
  key: string
  source: string
  session_id: string | null
  events: TimelineEvent[]
  latest_time: string
}

export function groupEvents(events: TimelineEvent[], aggregate = true): EventGroup[] {
  const groups: EventGroup[] = []
  for (const event of events) {
    const last = groups[groups.length - 1]
    if (aggregate && last && last.source === event.source && last.session_id === event.session_id) {
      last.events.push(event)
      last.latest_time = event.created_at
    } else {
      groups.push({
        key: event.id,
        source: event.source,
        session_id: event.session_id,
        events: [event],
        latest_time: event.created_at,
      })
    }
  }
  return groups
}

export function TimelineGroup({ group }: { group: EventGroup }) {
  const [expanded, setExpanded] = useState(false)
  const count = group.events.length

  if (count === 1) {
    return <TimelineEventRow event={group.events[0]} />
  }

  return (
    <div className="border-b border-[var(--color-border-subtle)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[var(--color-row-hover)] transition-colors text-sm"
      >
        <span className="text-xs text-text-tertiary font-mono w-[7.5rem] shrink-0">
          {formatDateTime(group.latest_time)}
        </span>
        <span className={`font-medium w-[10rem] shrink-0 ${getSourceColor(group.source)}`}>
          {group.source}
        </span>
        <span className="text-xs bg-zinc-400/10 text-zinc-400 px-1.5 py-0.5 rounded font-mono">
          ×{count}
        </span>
        <CaretDownIcon
          size={14}
          className={`ml-auto text-text-tertiary transition-transform shrink-0 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-[var(--color-border-subtle)] pl-6 border-l-2 border-l-[var(--color-border-subtle)] ml-4">
              {group.events.map((event) => (
                <TimelineEventRow key={event.id} event={event} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
