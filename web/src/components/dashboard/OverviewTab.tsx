import { GlassCard } from '@/components/GlassCard'
import { AnimatedNumber } from '@/components/AnimatedNumber'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts'

interface OverviewData {
  total_events: number
  total_errors: number
  events_by_source: { source: string; count: number }[]
  events_by_outcome: { outcome: string; count: number }[]
  error_summary: { role: string; count: number }[]
  pipeline_funnel: Record<string, number>
}

const CHART_COLORS = [
  '#38bdf8', '#34d399', '#a78bfa', '#fbbf24', '#fb7185',
  '#2dd4bf', '#f97316', '#818cf8', '#e879f9', '#94a3b8',
]

export function OverviewTab({ data }: { data: OverviewData | null }) {
  if (!data) return null

  const funnelData = Object.entries(data.pipeline_funnel)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-3 gap-4">
        <GlassCard>
          <div className="text-xs uppercase tracking-wider text-text-tertiary mb-1">Events</div>
          <div className="text-3xl font-mono font-semibold tabular-nums">
            <AnimatedNumber value={data.total_events} />
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-xs uppercase tracking-wider text-text-tertiary mb-1">Errors</div>
          <div className="text-3xl font-mono font-semibold tabular-nums text-[var(--color-error)]">
            <AnimatedNumber value={data.total_errors} />
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-xs uppercase tracking-wider text-text-tertiary mb-1">Sources</div>
          <div className="text-3xl font-mono font-semibold tabular-nums">
            <AnimatedNumber value={data.events_by_source.length} />
          </div>
        </GlassCard>
      </div>

      {/* Events by source bar chart */}
      {data.events_by_source.length > 0 && (
        <GlassCard>
          <h3 className="text-xs uppercase tracking-wider text-text-tertiary mb-4">Events by Source</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={data.events_by_source} layout="vertical" margin={{ left: 120 }}>
              <XAxis type="number" tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} />
              <YAxis
                type="category"
                dataKey="source"
                tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }}
                width={110}
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--color-bg-surface)',
                  border: '1px solid var(--color-border-subtle)',
                  borderRadius: 4,
                  fontSize: 12,
                }}
              />
              <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[0, 2, 2, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </GlassCard>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* Pipeline funnel */}
        {funnelData.length > 0 && (
          <GlassCard>
            <h3 className="text-xs uppercase tracking-wider text-text-tertiary mb-4">Pipeline Funnel</h3>
            <div className="space-y-2">
              {funnelData.map((item, i) => {
                const maxVal = funnelData[0]?.value ?? 1
                const pct = Math.round((item.value / maxVal) * 100)
                return (
                  <div key={item.name} className="flex items-center gap-3">
                    <span className="text-xs text-text-secondary w-24 shrink-0 truncate">{item.name}</span>
                    <div className="flex-1 h-5 bg-[var(--color-bg-surface)] rounded overflow-hidden">
                      <div
                        className="h-full rounded transition-all duration-500"
                        style={{
                          width: `${pct}%`,
                          background: CHART_COLORS[i % CHART_COLORS.length],
                        }}
                      />
                    </div>
                    <span className="text-xs font-mono text-text-tertiary w-8 text-right">{item.value}</span>
                  </div>
                )
              })}
            </div>
          </GlassCard>
        )}

        {/* Error pie chart */}
        {data.error_summary.length > 0 && (
          <GlassCard>
            <h3 className="text-xs uppercase tracking-wider text-text-tertiary mb-4">Errors by Role</h3>
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={data.error_summary}
                  dataKey="count"
                  nameKey="role"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={({ name, value }) => `${name}: ${value}`}
                  labelLine={false}
                >
                  {data.error_summary.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-bg-surface)',
                    border: '1px solid var(--color-border-subtle)',
                    borderRadius: 4,
                    fontSize: 12,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </GlassCard>
        )}
      </div>
    </div>
  )
}
