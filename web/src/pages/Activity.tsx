import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { FilterPill } from '@/components/FilterPill'
import { GlassCard } from '@/components/GlassCard'
import { EmptyState } from '@/components/EmptyState'
import { TimelineGroup } from '@/components/TimelineGroup'
import { OverviewTab } from '@/components/dashboard/OverviewTab'
import { ErrorsTab } from '@/components/dashboard/ErrorsTab'
import { TimeRangePicker } from '@/components/dashboard/TimeRangePicker'
import { usePolling } from '@/hooks/usePolling'
import { useApi } from '@/hooks/useApi'
import { fetchApi } from '@/lib/api'
import { t } from '@/lib/i18n'
import { hoursToISO } from '@/lib/timeRange'
import { groupEvents } from '@/lib/timelineGroups'
import type { TimelineEvent, TimelineSession } from '@/lib/types'

const POLL_INTERVAL = 5000
const EVENT_SOURCES = [
  'session_start',
  'user_prompt_submit',
  'pre_tool_use',
  'session_end',
  'cold_pipeline',
  'cli_extract',
  'cli_add',
  'cli_dismiss',
  'maintenance',
]

type Tab = 'timeline' | 'dashboard'
type DashboardSubTab = 'overview' | 'errors'

export function Activity() {
  const [activeTab, setActiveTab] = useState<Tab>('timeline')
  const [dashSubTab, setDashSubTab] = useState<DashboardSubTab>('overview')
  const [timeRangeHours, setTimeRangeHours] = useState(7 * 24)
  const [sessionFilter, setSessionFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const lastIdRef = useRef<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: sessionsData } = usePolling(
    () => fetchApi<{ sessions: TimelineSession[] }>('/timeline/sessions'),
    30000,
  )
  const sessions = sessionsData?.sessions ?? []

  const fetchEvents = useCallback(async (initial: boolean) => {
    const params: Record<string, string> = { limit: '100' }
    if (sessionFilter) params.session_id = sessionFilter
    if (sourceFilter) params.source = sourceFilter
    if (!initial && lastIdRef.current) params.after_id = lastIdRef.current
    if (initial) params.latest = 'true'
    const result = await fetchApi<{ events: TimelineEvent[]; has_more: boolean }>('/timeline', params)
    return result
  }, [sessionFilter, sourceFilter])

  const resetTimeline = () => {
    setEvents([])
    lastIdRef.current = null
  }

  useEffect(() => {
    lastIdRef.current = null
    let active = true
    let isFirst = true
    const poll = async () => {
      try {
        const result = await fetchEvents(isFirst)
        isFirst = false
        if (!active) return
        if (result.events.length > 0) {
          setEvents((prev: TimelineEvent[]) => {
            const existingIds = new Set(prev.map((e: TimelineEvent) => e.id))
            const newEvents = result.events.filter((e: TimelineEvent) => !existingIds.has(e.id))
            const combined = [...prev, ...newEvents]
            return combined.length > 1000 ? combined.slice(-1000) : combined
          })
          lastIdRef.current = result.events[result.events.length - 1].id
        }
      } catch {
        // fail-open
      }
    }
    void poll()
    const id = setInterval(poll, POLL_INTERVAL)
    return () => { active = false; clearInterval(id) }
  }, [fetchEvents])

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events, autoScroll])

  const groups = sourceFilter ? groupEvents(events, false) : groupEvents(events)

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      className="space-y-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight">{t('activity.title')}</h2>
      </div>

      {/* Tab bar */}
      <div className="flex gap-2 border-b border-[var(--color-border-subtle)] pb-0">
        <button
          onClick={() => setActiveTab('timeline')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'timeline'
              ? 'border-accent-sky text-[var(--color-text)]'
              : 'border-transparent text-text-secondary hover:text-[var(--color-text)]'
          }`}
        >
          {t('activity.tab.timeline')}
        </button>
        <button
          onClick={() => setActiveTab('dashboard')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'dashboard'
              ? 'border-accent-sky text-[var(--color-text)]'
              : 'border-transparent text-text-secondary hover:text-[var(--color-text)]'
          }`}
        >
          {t('activity.tab.dashboard')}
        </button>
      </div>

      {activeTab === 'timeline' ? (
        <>
          {/* Filters */}
          <div className="flex items-center gap-4 flex-wrap">
            <select
              value={sessionFilter}
              onChange={(e) => {
                resetTimeline()
                setSessionFilter(e.target.value)
              }}
              className="text-sm bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)] rounded px-3 py-1.5 text-[var(--color-text)] min-w-[12rem]"
            >
              <option value="">{t('activity.filter.all_sessions')}</option>
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {s.session_id.slice(0, 12)}... ({s.event_count})
                </option>
              ))}
            </select>

            <div className="flex gap-1.5 flex-wrap">
              <FilterPill
                active={sourceFilter === ''}
                label={t('activity.filter.all_types')}
                onClick={() => {
                  resetTimeline()
                  setSourceFilter('')
                }}
              />
              {EVENT_SOURCES.map((src) => (
                <FilterPill
                  key={src}
                  active={sourceFilter === src}
                  label={src.replace(/_/g, ' ')}
                  onClick={() => {
                    resetTimeline()
                    setSourceFilter(src)
                  }}
                />
              ))}
            </div>

            <label className="ml-auto flex items-center gap-2 text-xs text-text-tertiary cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded"
              />
              {t('activity.auto_scroll')}
            </label>
          </div>

          {/* Event timeline */}
          <GlassCard>
            <div
              ref={scrollRef}
              className="max-h-[calc(100vh-20rem)] overflow-y-auto"
            >
              {groups.length === 0 ? (
                <EmptyState message={t('activity.empty')} />
              ) : (
                groups.map((group) => (
                  <TimelineGroup key={group.key} group={group} />
                ))
              )}
            </div>
          </GlassCard>
        </>
      ) : (
        <DashboardContent
          subTab={dashSubTab}
          setSubTab={setDashSubTab}
          timeRangeHours={timeRangeHours}
          setTimeRangeHours={setTimeRangeHours}
          sessionFilter={sessionFilter}
        />
      )}
    </motion.div>
  )
}

function DashboardContent({
  subTab,
  setSubTab,
  timeRangeHours,
  setTimeRangeHours,
  sessionFilter,
}: {
  subTab: DashboardSubTab
  setSubTab: (t: DashboardSubTab) => void
  timeRangeHours: number
  setTimeRangeHours: (h: number) => void
  sessionFilter: string
}) {
  const since = hoursToISO(timeRangeHours)
  const params: Record<string, string> = { since }
  if (sessionFilter) params.session_id = sessionFilter

  const { data: overviewData } = useApi<{
    total_events: number
    total_errors: number
    events_by_source: { source: string; count: number }[]
    events_by_outcome: { outcome: string; count: number }[]
    error_summary: { role: string; count: number }[]
    pipeline_funnel: Record<string, number>
  }>('/monitor/overview', params)

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <TimeRangePicker value={timeRangeHours} onChange={setTimeRangeHours} />
      </div>

      <div className="flex gap-2 border-b border-[var(--color-border-subtle)] pb-0">
        <button
          onClick={() => setSubTab('overview')}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
            subTab === 'overview'
              ? 'border-accent-sky text-[var(--color-text)]'
              : 'border-transparent text-text-secondary hover:text-[var(--color-text)]'
          }`}
        >
          {t('activity.dashboard.overview')}
        </button>
        <button
          onClick={() => setSubTab('errors')}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
            subTab === 'errors'
              ? 'border-accent-sky text-[var(--color-text)]'
              : 'border-transparent text-text-secondary hover:text-[var(--color-text)]'
          }`}
        >
          {t('activity.dashboard.errors')}
        </button>
      </div>

      {subTab === 'overview' ? (
        <OverviewTab data={overviewData} />
      ) : (
        <ErrorsTab since={since} sessionId={sessionFilter} />
      )}
    </div>
  )
}
