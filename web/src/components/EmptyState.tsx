import { motion } from 'motion/react'

interface EmptyStateProps {
  message: string
  className?: string
}

export function EmptyState({ message, className = '' }: EmptyStateProps) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] as const }}
      className={`flex flex-col items-center justify-center py-12 ${className}`}
    >
      <motion.svg
        width="64"
        height="64"
        viewBox="0 0 64 64"
        fill="none"
        className="text-text-muted mb-4"
        animate={{ y: [0, -4, 0] }}
        transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
      >
        <rect x="12" y="16" width="40" height="36" rx="3" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 2" />
        <path d="M24 32h16M24 38h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.5" />
        <circle cx="32" cy="12" r="3" stroke="currentColor" strokeWidth="1.5" />
      </motion.svg>
      <p className="text-sm text-text-tertiary">{message}</p>
    </motion.div>
  )
}
