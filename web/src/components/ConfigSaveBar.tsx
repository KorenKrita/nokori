import { motion } from 'motion/react'
import { t } from '@/lib/i18n'

interface Props {
  dirty: boolean
  saving: boolean
  onCancel: () => void
  onSave: () => void
  className?: string
}

export function ConfigSaveBar({ dirty, saving, onCancel, onSave, className = '' }: Props) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      {dirty && (
        <span className="text-xs text-[var(--color-text-tertiary)] mr-1 hidden sm:inline">
          {t('config.unsaved')}
        </span>
      )}
      <button
        type="button"
        className="px-4 py-2 rounded-full text-sm border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-row-hover)] disabled:opacity-40 transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
        disabled={!dirty || saving}
        onClick={onCancel}
      >
        {t('config.cancel')}
      </button>
      <motion.button
        type="button"
        disabled={!dirty || saving}
        whileHover={dirty && !saving ? { scale: 1.03 } : undefined}
        whileTap={dirty && !saving ? { scale: 0.97 } : undefined}
        className="px-4 py-2 rounded-full text-sm font-medium bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)] disabled:opacity-40 transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
        onClick={onSave}
      >
        {saving ? t('config.saving') : t('config.save')}
      </motion.button>
    </div>
  )
}
