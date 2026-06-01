import { useRef, useState } from 'react'
import { motion } from 'motion/react'
import { cn } from '@/lib/utils'

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  hover?: boolean
}

export function GlassCard({ children, className, hover = false }: GlassCardProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [spotlightPos, setSpotlightPos] = useState({ x: 0, y: 0 })
  const [isHovered, setIsHovered] = useState(false)

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current || !hover) return
    const rect = ref.current.getBoundingClientRect()
    setSpotlightPos({ x: e.clientX - rect.left, y: e.clientY - rect.top })
  }

  const cardClasses = cn(
    'rounded-2xl relative overflow-hidden',
    'bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)]',
    'shadow-[var(--color-card-shadow)]',
    className
  )

  if (hover) {
    return (
      <motion.div
        ref={ref}
        className={cardClasses}
        onMouseMove={handleMouseMove}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        whileHover={{
          scale: 1.006,
          transition: { duration: 0.4, ease: [0.32, 0.72, 0, 1] },
        }}
      >
        {isHovered && (
          <div
            className="absolute inset-0 rounded-2xl pointer-events-none transition-opacity duration-300"
            style={{
              background: `radial-gradient(300px circle at ${spotlightPos.x}px ${spotlightPos.y}px, rgba(56,189,248,0.04), transparent 60%)`,
            }}
          />
        )}
        <div className="relative p-5 h-full">
          {children}
        </div>
      </motion.div>
    )
  }

  return (
    <div className={cardClasses}>
      <div className="p-5 h-full">
        {children}
      </div>
    </div>
  )
}
