import { cn } from '@/lib/utils'

type Variant = 'success' | 'error' | 'info'

const STYLES: Record<Variant, string> = {
  success: 'text-accent-emerald border-accent-emerald/25 bg-accent-emerald/10',
  error: 'text-accent-rose border-accent-rose/25 bg-accent-rose/10',
  info: 'text-accent-sky border-accent-sky/25 bg-accent-sky/10',
}

interface Props {
  variant: Variant
  children: React.ReactNode
  className?: string
}

export function Alert({ variant, children, className }: Props) {
  return (
    <p
      role="status"
      className={cn(
        'text-sm rounded-xl px-3 py-2.5 border leading-relaxed',
        STYLES[variant],
        className,
      )}
    >
      {children}
    </p>
  )
}
