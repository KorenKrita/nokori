import { useState, useCallback, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'motion/react'
import { GlassCard } from '@/components/GlassCard'
import { PageSkeleton } from '@/components/PageSkeleton'
import { useApi } from '@/hooks/useApi'
import { fetchApi } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDateTime'
import { barrierLabel, maintenanceJobLabel, statusLabel, t, lz } from '@/lib/i18n'
import { displayProjectName } from '@/lib/displayProjectName'

interface PromotionData {
  data: {
    enabled: boolean
    candidates: {
      short_id: string
      project_id: string
      trigger_canonical?: string
      trigger_canonical_zh?: string | null
      status: string
      rule_version: number
      quality_score: number
    }[]
  }
}
interface GlobalEligibleRule {
  short_id: string
  trigger_canonical?: string
  trigger_canonical_zh?: string | null
  project_id: string | null
  distinct_projects: number
  target: number
}
interface GlobalEligibleData { data: GlobalEligibleRule[] }
interface MaintenanceData { data: Record<string, string> }

interface BarrierThreshold {
  name: string
  current: number
  target: number
  met: boolean
  direction: 'min' | 'max'
}

interface BarriersData {
  data: {
    current_state: string
    target_state: string
    thresholds: BarrierThreshold[]
    blocking: string | null
  } | null
}

interface BarriersState {
  loading: boolean
  error: string | null
  data: BarriersData['data'] | null
}

function formatLastRun(value: string): string {
  if (!value || value === 'never') return t('lifecycle.last_run_never')
  return formatDateTime(value) || value
}

function BarriersPanel({ state, onRetry }: { state: BarriersState; onRetry: () => void }) {
  if (state.loading) {
    return <p className="text-xs text-text-tertiary mt-2">{t('lifecycle.barriers_loading')}</p>
  }
  if (state.error) {
    return (
      <p className="text-xs text-red-400 mt-2">
        {t('lifecycle.barriers_error')}{' '}
        <button type="button" onClick={onRetry} className="underline text-text-tertiary hover:text-text-secondary">↻</button>
      </p>
    )
  }
  if (!state.data) {
    return <p className="text-xs text-text-tertiary mt-2">{t('lifecycle.barriers_none')}</p>
  }

  const { thresholds, target_state, blocking } = state.data

  return (
    <div className="mt-3 space-y-1.5">
      <div className="flex items-center gap-2 text-xs text-text-tertiary">
        <span>{t('lifecycle.target')}: <span className="font-mono text-text-secondary">{statusLabel(target_state)}</span></span>
        {blocking && (
          <span className="text-amber-400">{t('lifecycle.blocking')}: <span className="font-mono">{barrierLabel(blocking)}</span></span>
        )}
      </div>
      {(thresholds ?? []).map((th) => {
        let pct: number
        if (th.met) {
          pct = 100
        } else if (th.direction === 'max') {
          pct = 0
        } else if (th.target > 0) {
          pct = Math.min(100, Math.max(0, (th.current / th.target) * 100))
        } else {
          pct = 0
        }
        const isBlocking = th.name === blocking
        let barColor = 'bg-[var(--color-text-tertiary)]'
        if (th.met) barColor = 'bg-emerald-500'
        else if (isBlocking) barColor = 'bg-amber-500'
        let labelColor = 'text-text-secondary'
        if (isBlocking) labelColor = 'text-amber-400'
        else if (th.met) labelColor = 'text-emerald-400'
        return (
          <div key={th.name} className="flex items-center gap-2">
            <span className={`text-xs font-mono w-56 truncate shrink-0 ${labelColor}`}>
              {barrierLabel(th.name)}
            </span>
            <div className="flex-1 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
              <motion.div
                className={`h-full rounded-full ${barColor}`}
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] as const }}
              />
            </div>
            <span className={`text-xs font-mono w-14 text-right shrink-0 ${th.met ? 'text-emerald-400' : 'text-text-tertiary'}`}>
              {th.current}/{th.target}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function GlobalEligibleList({ data }: { data: GlobalEligibleData['data'] }) {
  if (data.length === 0) {
    return <p className="text-sm text-text-tertiary">{t('lifecycle.no_global_eligible')}</p>
  }
  return (
    <>
      {data.map((r) => (
        <div key={r.short_id} className="border-b border-[var(--color-border-subtle)] py-3 last:border-0">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Link
                to={`/rules/${r.short_id}`}
                className="font-mono text-xs text-accent-sky hover:underline"
              >
                {r.short_id}
              </Link>
              {r.project_id && (
                <span title={r.project_id} className="text-xs text-text-tertiary font-mono truncate max-w-[10rem]">
                  {displayProjectName(r.project_id)}
                </span>
              )}
            </div>
            <span className="text-xs font-mono text-emerald-400">
              {r.distinct_projects}/{r.target} {t('lifecycle.global_eligible_projects')}
            </span>
          </div>
          <p className="text-sm text-text-secondary mt-1 truncate">{lz(r.trigger_canonical, r.trigger_canonical_zh)}</p>
        </div>
      ))}
    </>
  )
}

export function Lifecycle() {
  const { data: promo, isLoading: l1 } = useApi<PromotionData>('/lifecycle/promotion')
  const { data: maint, isLoading: l2 } = useApi<MaintenanceData>('/lifecycle/maintenance')
  const { data: globalEligible, isLoading: l3 } = useApi<GlobalEligibleData>('/lifecycle/global-eligible')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [barriersCache, setBarriersCache] = useState<Record<string, BarriersState>>({})
  const loadingRef = useRef<Set<string>>(new Set())
  const loadedRef = useRef<Set<string>>(new Set())

  const loadBarriers = useCallback(async (shortId: string) => {
    if (loadingRef.current.has(shortId)) return
    if (loadedRef.current.has(shortId)) return
    setBarriersCache(prev => ({ ...prev, [shortId]: { loading: true, error: null, data: null } }))
    loadingRef.current.add(shortId)
    try {
      const result = await fetchApi<BarriersData>(`/lifecycle/rules/${shortId}/barriers`)
      loadedRef.current.add(shortId)
      setBarriersCache(prev => ({ ...prev, [shortId]: { loading: false, error: null, data: result.data } }))
    } catch (e: unknown) {
      setBarriersCache(prev => ({
        ...prev,
        [shortId]: { loading: false, error: e instanceof Error ? e.message : 'Unknown error', data: null },
      }))
    } finally {
      loadingRef.current.delete(shortId)
    }
  }, [])

  const retryBarriers = useCallback((shortId: string) => {
    loadingRef.current.delete(shortId)
    loadedRef.current.delete(shortId)
    setBarriersCache(prev => ({
      ...prev,
      [shortId]: { loading: true, error: null, data: null },
    }))
    void loadBarriers(shortId)
  }, [loadBarriers])

  useEffect(() => {
    if (expandedId) {
      void loadBarriers(expandedId)
    }
  }, [expandedId, loadBarriers])

  if (l1 || l2 || l3) return <PageSkeleton />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      className="space-y-6"
    >
      <h2 className="text-2xl font-semibold tracking-tight">{t('lifecycle.title')}</h2>

      <GlassCard hover>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">{t('lifecycle.promotion')}</h3>
        {!promo?.data.enabled && <p className="text-sm text-text-tertiary">{t('lifecycle.promotion_disabled')}</p>}
        {promo?.data.enabled && promo.data.candidates.length === 0 && (
          <p className="text-sm text-text-tertiary">{t('lifecycle.no_candidates')}</p>
        )}
        {promo?.data.candidates.map((c) => (
          <div key={c.short_id} className="border-b border-[var(--color-border-subtle)] py-3 last:border-0">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Link
                  to={`/rules/${c.short_id}`}
                  className="font-mono text-xs text-accent-sky hover:underline"
                >
                  {c.short_id}
                </Link>
                {c.project_id && (
                  <span title={c.project_id} className="text-xs text-text-tertiary font-mono truncate max-w-[10rem]">
                    {displayProjectName(c.project_id)}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => setExpandedId(expandedId === c.short_id ? null : c.short_id)}
                className="text-xs text-text-tertiary hover:text-text-secondary transition-colors"
              >
                {expandedId === c.short_id ? '▲' : '▼'}
              </button>
            </div>
            <p className="text-sm text-text-secondary mt-1 truncate">{lz(c.trigger_canonical, c.trigger_canonical_zh)}</p>
            <AnimatePresence>
              {expandedId === c.short_id && (
                <motion.div
                  key={c.short_id}
                  className="overflow-hidden"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <BarriersPanel
                    state={barriersCache[c.short_id] ?? { loading: true, error: null, data: null }}
                    onRetry={() => retryBarriers(c.short_id)}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </GlassCard>

      <GlassCard hover>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">{t('lifecycle.global_eligible')}</h3>
        <GlobalEligibleList data={globalEligible?.data ?? []} />
      </GlassCard>

      <GlassCard>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-4">{t('lifecycle.maintenance')}</h3>
        <div className="space-y-2">
          {Object.entries(maint?.data ?? {}).map(([key, lastRun]) => (
            <div key={key} className="flex justify-between gap-4 py-1">
              <span className="text-sm text-[var(--color-text-primary)]">{maintenanceJobLabel(key)}</span>
              <span className="text-xs font-mono text-text-tertiary whitespace-nowrap shrink-0">
                {formatLastRun(lastRun)}
              </span>
            </div>
          ))}
          {Object.keys(maint?.data ?? {}).length === 0 && (
            <p className="text-sm text-text-tertiary">{t('lifecycle.no_maintenance')}</p>
          )}
        </div>
      </GlassCard>
    </motion.div>
  )
}
