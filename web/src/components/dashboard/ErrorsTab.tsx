import { GlassCard } from '@/components/GlassCard'
import { useApi } from '@/hooks/useApi'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
  BarChart, Bar,
} from 'recharts'

const CHART_COLORS = [
  '#fb7185', '#fbbf24', '#38bdf8', '#a78bfa', '#34d399',
  '#f97316', '#818cf8', '#e879f9',
]

interface ErrorTrendItem {
  day: string
  error_type: string
  count: number
}

interface ErrorGroupItem {
  role?: string
  model_id?: string
  error_type?: string
  count: number
}

export function ErrorsTab({ since }: { since: string }) {
  const { data: trendData } = useApi<{ trend: ErrorTrendItem[] }>('/monitor/errors/trend', { since })
  const { data: byRole } = useApi<{ errors: ErrorGroupItem[] }>('/monitor/errors', { group_by: 'role', since })
  const { data: byModel } = useApi<{ errors: ErrorGroupItem[] }>('/monitor/errors', { group_by: 'model_id', since })

  const trend = trendData?.trend ?? []
  const roleErrors = byRole?.errors ?? []
  const modelErrors = byModel?.errors ?? []

  const trendByDay = groupTrendByDay(trend)

  return (
    <div className="space-y-6">
      {/* Trend line chart */}
      {trendByDay.length > 0 && (
        <GlassCard>
          <h3 className="text-xs uppercase tracking-wider text-text-tertiary mb-4">Error Trend</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={trendByDay}>
              <XAxis dataKey="day" tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} />
              <YAxis tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} />
              <Tooltip
                contentStyle={{
                  background: 'var(--color-bg-surface)',
                  border: '1px solid var(--color-border-subtle)',
                  borderRadius: 4,
                  fontSize: 12,
                }}
              />
              <Legend />
              {getErrorTypes(trend).map((type, i) => (
                <Line
                  key={type}
                  type="monotone"
                  dataKey={type}
                  stroke={CHART_COLORS[i % CHART_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </GlassCard>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* Errors by role */}
        {roleErrors.length > 0 && (
          <GlassCard>
            <h3 className="text-xs uppercase tracking-wider text-text-tertiary mb-4">By Role</h3>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={roleErrors} layout="vertical" margin={{ left: 80 }}>
                <XAxis type="number" tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} />
                <YAxis type="category" dataKey="role" tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} width={70} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-bg-surface)',
                    border: '1px solid var(--color-border-subtle)',
                    borderRadius: 4,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="count" fill="#fb7185" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </GlassCard>
        )}

        {/* Errors by model */}
        {modelErrors.length > 0 && (
          <GlassCard>
            <h3 className="text-xs uppercase tracking-wider text-text-tertiary mb-4">By Model</h3>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={modelErrors} layout="vertical" margin={{ left: 100 }}>
                <XAxis type="number" tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} />
                <YAxis type="category" dataKey="model_id" tick={{ fill: 'var(--color-text-secondary)', fontSize: 11 }} width={90} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-bg-surface)',
                    border: '1px solid var(--color-border-subtle)',
                    borderRadius: 4,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="count" fill="#fbbf24" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </GlassCard>
        )}
      </div>

      {/* Empty state */}
      {roleErrors.length === 0 && modelErrors.length === 0 && trend.length === 0 && (
        <GlassCard>
          <div className="text-center text-text-tertiary py-12 text-sm">
            No errors recorded in this time range.
          </div>
        </GlassCard>
      )}
    </div>
  )
}

function getErrorTypes(trend: ErrorTrendItem[]): string[] {
  return [...new Set(trend.map((t) => t.error_type))]
}

function groupTrendByDay(trend: ErrorTrendItem[]): Record<string, unknown>[] {
  const byDay: Record<string, Record<string, number>> = {}
  for (const item of trend) {
    if (!byDay[item.day]) byDay[item.day] = {}
    byDay[item.day][item.error_type] = (byDay[item.day][item.error_type] || 0) + item.count
  }
  return Object.entries(byDay).map(([day, types]) => ({ day, ...types }))
}
