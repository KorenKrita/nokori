import { useRef } from 'react'
import { motion, useMotionValue, useTransform } from 'motion/react'
import { cn } from '@/lib/utils'

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  hover?: boolean
}

export function GlassCard({ children, className, hover = false }: GlassCardProps) {
  const ref = useRef<HTMLDivElement>(null)
  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)

  const spotlight = useTransform(
    [mouseX, mouseY],
    ([x, y]) => `radial-gradient(300px circle at ${x}px ${y}px, rgba(255,255,255,0.06), transparent 60%)`
  )

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current || !hover) return
    const rect = ref.current.getBoundingClientRect()
    mouseX.set(e.clientX - rect.left)
    mouseY.set(e.clientY - rect.top)
  }

  if (hover) {
    return (
      <motion.div
        ref={ref}
        className={cn(
          'rounded-2xl p-[1px] relative group overflow-hidden',
          'bg-[var(--color-bg-surface)] ring-1 ring-[var(--color-border-subtle)]',
          className
        )}
        onMouseMove={handleMouseMove}
        whileHover={{
          scale: 1.006,
          transition: { duration: 0.4, ease: [0.32, 0.72, 0, 1] },
        }}
      >
        <motion.div
          className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
          style={{ background: spotlight }}
        />
        <div className="relative rounded-[15px] bg-[var(--color-bg-surface)] backdrop-blur-xl p-5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.04)] h-full">
          {children}
        </div>
      </motion.div>
    )
  }

  return (
    <div
      className={cn(
        'rounded-2xl p-[1px]',
        'bg-[var(--color-bg-surface)] ring-1 ring-[var(--color-border-subtle)]',
        className
      )}
    >
      <div className="rounded-[15px] bg-[var(--color-bg-surface)] backdrop-blur-xl p-5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.04)] h-full">
        {children}
      </div>
    </div>
  )
}
