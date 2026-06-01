import { motion } from 'motion/react'
import { cn } from '@/lib/utils'

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  hover?: boolean
}

export function GlassCard({ children, className, hover = false }: GlassCardProps) {
  if (hover) {
    return (
      <motion.div
        className={cn(
          'rounded-2xl p-[1px] relative group',
          'bg-white/[0.03] ring-1 ring-white/[0.06]',
          className
        )}
        whileHover={{
          scale: 1.008,
          boxShadow: '0 0 30px rgba(56, 189, 248, 0.03), inset 0 1px 1px rgba(255,255,255,0.1)',
        }}
        transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
      >
        <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-white/[0.02] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
        <div className="relative rounded-[15px] bg-white/[0.02] backdrop-blur-xl p-5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)] h-full">
          {children}
        </div>
      </motion.div>
    )
  }

  return (
    <div
      className={cn(
        'rounded-2xl p-[1px]',
        'bg-white/[0.03] ring-1 ring-white/[0.06]',
        className
      )}
    >
      <div className="rounded-[15px] bg-white/[0.02] backdrop-blur-xl p-5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)] h-full">
        {children}
      </div>
    </div>
  )
}
