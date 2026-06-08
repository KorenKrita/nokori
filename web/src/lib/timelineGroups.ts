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
