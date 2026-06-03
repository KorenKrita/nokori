const shimmerStyle = {
  background: 'linear-gradient(90deg, var(--color-bg-elevated) 25%, rgba(255,255,255,0.06) 50%, var(--color-bg-elevated) 75%)',
  backgroundSize: '200% 100%',
  animation: 'shimmer 1.5s ease-in-out infinite',
}

export function PageSkeleton() {
  return (
    <div className="space-y-4">
      <div className="h-8 w-48 rounded-lg" style={shimmerStyle} />
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-8 h-40 rounded border border-[var(--color-border-subtle)]" style={shimmerStyle} />
        <div className="col-span-4 h-40 rounded border border-[var(--color-border-subtle)]" style={shimmerStyle} />
        <div className="col-span-4 h-32 rounded border border-[var(--color-border-subtle)]" style={shimmerStyle} />
        <div className="col-span-4 h-32 rounded border border-[var(--color-border-subtle)]" style={shimmerStyle} />
        <div className="col-span-4 h-32 rounded border border-[var(--color-border-subtle)]" style={shimmerStyle} />
      </div>
    </div>
  )
}
