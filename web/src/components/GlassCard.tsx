import { motion } from 'motion/react'
import { cn } from '@/lib/utils'

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  hover?: boolean
}

export function GlassCard({ children, className, hover = false }: GlassCardProps) {
  const Wrapper = hover ? motion.div : 'div'
  const motionProps = hover
    ? { whileHover: { scale: 1.005 }, transition: { duration: 0.3, ease: [0.32, 0.72, 0, 1] } }
    : {}

  return (
    <Wrapper
      className={cn(
        'rounded-2xl p-[1px]',
        'bg-white/[0.03] ring-1 ring-white/[0.06]',
        className
      )}
      {...motionProps}
    >
      <div className="rounded-[15px] bg-white/[0.02] backdrop-blur-xl p-5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)] h-full">
        {children}
      </div>
    </Wrapper>
  )
}
