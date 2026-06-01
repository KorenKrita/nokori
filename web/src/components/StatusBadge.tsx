import { cn } from '@/lib/utils'

const VARIANTS: Record<string, string> = {
  active: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/20',
  dormant: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/20',
  candidate: 'bg-violet-500/15 text-violet-300 border-violet-500/20',
  merged: 'bg-sky-500/15 text-sky-300 border-sky-500/20',
  archived: 'bg-zinc-800/50 text-zinc-500 border-zinc-700/30',
  hot: 'bg-rose-500/20 text-rose-300 border-rose-500/20',
  warm: 'bg-amber-500/20 text-amber-300 border-amber-500/20',
  ok: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/20',
  warn: 'bg-amber-500/15 text-amber-300 border-amber-500/20',
  fail: 'bg-rose-500/15 text-rose-300 border-rose-500/20',
  skip: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/20',
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
        'inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium uppercase tracking-wider border',
        variant,
        className
      )}
    >
      {status}
    </span>
  )
}
