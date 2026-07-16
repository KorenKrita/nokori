import { describe, expect, it } from 'vitest'
import { groupEvents } from './timelineGroups'
import type { TimelineEvent } from './types'

function ev(partial: Partial<TimelineEvent> & Pick<TimelineEvent, 'id' | 'source'>): TimelineEvent {
  return {
    session_id: null,
    outcome: null,
    prompt_snippet: null,
    details: null,
    created_at: '2026-01-01T00:00:00Z',
    ...partial,
  }
}

describe('groupEvents', () => {
  it('aggregates consecutive same source+session', () => {
    const events = [
      ev({ id: '1', source: 'hook', session_id: 's1', created_at: 't1' }),
      ev({ id: '2', source: 'hook', session_id: 's1', created_at: 't2' }),
      ev({ id: '3', source: 'extract', session_id: 's1', created_at: 't3' }),
    ]
    const groups = groupEvents(events)
    expect(groups).toHaveLength(2)
    expect(groups[0].events).toHaveLength(2)
    expect(groups[0].latest_time).toBe('t2')
    expect(groups[1].source).toBe('extract')
  })

  it('does not aggregate when aggregate=false', () => {
    const events = [
      ev({ id: '1', source: 'hook', session_id: 's1' }),
      ev({ id: '2', source: 'hook', session_id: 's1' }),
    ]
    expect(groupEvents(events, false)).toHaveLength(2)
  })
})
