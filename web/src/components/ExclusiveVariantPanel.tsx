import { motion, AnimatePresence } from 'motion/react'
import { CpuIcon, CloudIcon } from '@phosphor-icons/react'
import type { ConfigVariantSchema, ConfigValues } from '@/lib/configTypes'
import { ConfigField } from '@/components/ConfigField'
import { t } from '@/lib/i18n'
import { cn } from '@/lib/utils'

interface Props {
  variants: ConfigVariantSchema[]
  active: 'local' | 'remote'
  onRequestActiveChange: (id: 'local' | 'remote') => void
  values: ConfigValues
  onChange: (id: string, value: string | number | boolean | null) => void
  setKeys: Set<string>
  secretsSet: Set<string>
  envLocked: Set<string>
  clearsOnSave?: string[]
}

const VARIANT_ICON = {
  local: CpuIcon,
  remote: CloudIcon,
} as const

export function ExclusiveVariantPanel({
  variants,
  active,
  onRequestActiveChange,
  values,
  onChange,
  setKeys,
  secretsSet,
  envLocked,
  clearsOnSave = [],
}: Props) {
  const select = (id: 'local' | 'remote') => {
    if (id === active) {
      return
    }
    onRequestActiveChange(id)
  }

  const removeHint =
    active === 'local' ? t('config.remove_keys_local') : t('config.remove_keys_remote')

  return (
    <div className="space-y-3 mt-4 pt-4 border-t border-[var(--color-border-subtle)]">
      <div>
        <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
          {t('config.embed_backend_title')}
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)] mt-1 leading-relaxed">
          {t('config.embed_exclusive_hint')}
        </p>
      </div>

      <div
        className="rounded-lg px-3 py-2 text-xs leading-relaxed border border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
        role="note"
      >
        <span className="text-accent-amber font-medium">{t('config.remove_on_save_label')}</span>
        {' '}
        {removeHint}
        {clearsOnSave.length > 0 && (
          <span className="mt-1 block font-mono text-[10px] text-[var(--color-text-tertiary)]">
            {clearsOnSave.join(' · ')}
          </span>
        )}
      </div>

      <div className="space-y-2">
        {variants.map((variant) => {
          const isActive = active === variant.id
          const Icon = VARIANT_ICON[variant.id]
          return (
            <div
              key={variant.id}
              className={cn(
                'rounded-xl border overflow-hidden transition-colors duration-300',
                isActive
                  ? 'border-accent-sky/40 shadow-[var(--color-card-shadow)]'
                  : 'border-[var(--color-border-subtle)]',
              )}
            >
              <button
                type="button"
                className={cn(
                  'w-full flex items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]',
                  isActive
                    ? 'bg-[var(--color-nav-active-bg)] text-[var(--color-nav-active-text)] border-l-2 border-accent-sky'
                    : 'bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)] hover:bg-[var(--color-row-hover)]',
                )}
                onClick={() => select(variant.id)}
                aria-expanded={isActive}
              >
                <span className="flex items-center gap-2">
                  <Icon size={18} weight="light" className={isActive ? 'text-accent-sky' : ''} />
                  {variant.label}
                  {isActive && (
                    <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded-full bg-accent-sky/15 text-accent-sky">
                      {t('config.active_mode')}
                    </span>
                  )}
                </span>
                <span className="text-[var(--color-text-tertiary)] text-xs">{isActive ? '▾' : '▸'}</span>
              </button>
              <AnimatePresence initial={false}>
                {isActive && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] as const }}
                    className="overflow-hidden border-t border-[var(--color-border-subtle)]"
                  >
                    <div className="px-4 pb-2 bg-[var(--color-bg-surface)]">
                      {variant.fields.map((field) => (
                        <ConfigField
                          key={field.id}
                          field={field}
                          value={values[field.id]}
                          onChange={onChange}
                          envLocked={envLocked.has(field.id)}
                          secretConfigured={secretsSet.has(field.id)}
                          setInFile={setKeys.has(field.id)}
                        />
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )
        })}
      </div>
    </div>
  )
}
