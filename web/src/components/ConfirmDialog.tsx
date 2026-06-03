import { useCallback, useEffect, useRef } from 'react'
import { motion } from 'motion/react'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  title: string
  message: string
  confirmLabel: string
  cancelLabel: string
  variant?: 'default' | 'danger'
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  variant = 'default',
  onConfirm,
  onCancel,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null)

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      onCancel()
      return
    }
    if (e.key === 'Tab') {
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
      if (!focusable || focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }, [onCancel])

  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    return () => { prev?.focus() }
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={onCancel}
      onKeyDown={handleKeyDown}
    >
      <motion.div
        ref={dialogRef}
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] as const }}
        className="w-full max-w-md rounded border border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] p-5 shadow-[var(--color-card-shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h4 id="confirm-dialog-title" className="text-base font-semibold text-[var(--color-text-primary)]">
          {title}
        </h4>
        <p className="text-sm text-[var(--color-text-secondary)] mt-2 leading-relaxed">{message}</p>
        <div className="flex justify-end gap-2 mt-5">
          <button
            type="button"
            autoFocus
            className="px-4 py-2 rounded-full text-sm border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-row-hover)] transition-colors"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={cn(
              'px-4 py-2 rounded-full text-sm font-medium transition-colors',
              variant === 'danger'
                ? 'bg-accent-rose/15 text-accent-rose hover:bg-accent-rose/25'
                : 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]',
            )}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
