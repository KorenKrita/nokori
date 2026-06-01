import { cn } from '@/lib/utils'

interface StatusDotProps {
  running: boolean
}

export function StatusDot({ running }: StatusDotProps) {
  return (
    <span className="relative flex h-2.5 w-2.5">
      {running && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-50" />
      )}
      <span
        className={cn(
          'relative inline-flex h-2.5 w-2.5 rounded-full',
          running ? 'bg-emerald-400' : 'bg-zinc-600'
        )}
      />
    </span>
  )
}
