import { cn } from '@/lib/utils'

const VARIANTS: Record<string, string> = {
  active: 'bg-accent-emerald/15 text-accent-emerald border-accent-emerald/25',
  trusted: 'bg-accent-sky/15 text-accent-sky border-accent-sky/25',
  candidate: 'bg-accent-violet/15 text-accent-violet border-accent-violet/25',
  suppressed: 'bg-accent-amber/15 text-accent-amber border-accent-amber/25',
  archived: 'bg-[var(--color-bg-elevated)] text-text-muted border-[var(--color-border-subtle)]',
  hot: 'bg-accent-rose/20 text-accent-rose border-accent-rose/25',
  warm: 'bg-accent-amber/20 text-accent-amber border-accent-amber/25',
  ok: 'bg-accent-emerald/15 text-accent-emerald border-accent-emerald/25',
  warn: 'bg-accent-amber/15 text-accent-amber border-accent-amber/25',
  fail: 'bg-accent-rose/15 text-accent-rose border-accent-rose/25',
  skip: 'bg-[var(--color-bg-elevated)] text-text-muted border-[var(--color-border-subtle)]',
}

interface StatusBadgeProps {
  status: string
  className?: string
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const variant = VARIANTS[status] ?? VARIANTS.skip
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-sm text-[11px] font-medium uppercase tracking-wider border',
        variant,
        className
      )}
    >
      {status}
    </span>
  )
}
