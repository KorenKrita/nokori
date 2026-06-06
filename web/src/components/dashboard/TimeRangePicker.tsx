import { FilterPill } from '@/components/FilterPill'

const PRESETS = [
  { label: '1h', hours: 1 },
  { label: '3h', hours: 3 },
  { label: '1d', hours: 24 },
  { label: '7d', hours: 168 },
  { label: '30d', hours: 720 },
]

function hoursToISO(hours: number): string {
  return new Date(Date.now() - hours * 3600_000).toISOString().replace(/\.\d{3}Z$/, 'Z')
}

interface TimeRangePickerProps {
  value: number
  onChange: (hours: number) => void
}

export function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  return (
    <div className="flex gap-1.5">
      {PRESETS.map((p) => (
        <FilterPill
          key={p.label}
          active={value === p.hours}
          label={p.label}
          onClick={() => onChange(p.hours)}
        />
      ))}
    </div>
  )
}

export { hoursToISO }
