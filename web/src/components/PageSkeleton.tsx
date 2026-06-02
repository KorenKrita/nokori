export function PageSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-8 w-48 rounded-lg bg-[var(--color-bg-elevated)]" />
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-8 h-40 rounded-2xl bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)]" />
        <div className="col-span-4 h-40 rounded-2xl bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)]" />
        <div className="col-span-4 h-32 rounded-2xl bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)]" />
        <div className="col-span-4 h-32 rounded-2xl bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)]" />
        <div className="col-span-4 h-32 rounded-2xl bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)]" />
      </div>
    </div>
  )
}
