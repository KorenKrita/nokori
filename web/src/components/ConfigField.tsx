import { useState } from 'react'
import { CopyIcon, CheckIcon } from '@phosphor-icons/react'
import type { ConfigFieldSchema } from '@/lib/configTypes'
import { t } from '@/lib/i18n'

interface Props {
  field: ConfigFieldSchema
  value: string | number | boolean | null | undefined
  onChange: (id: string, value: string | number | boolean | null) => void
  envLocked: boolean
  secretConfigured: boolean
  setInFile: boolean
}

const inputClass =
  'w-full rounded-lg px-3 py-2 text-sm bg-[var(--color-input-bg)] border border-[var(--color-input-border)] text-[var(--color-text-primary)] placeholder:text-text-tertiary focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)] focus:border-transparent disabled:opacity-50 transition-shadow duration-200'

export function ConfigField({
  field,
  value,
  onChange,
  envLocked,
  secretConfigured,
  setInFile,
}: Props) {
  const disabled = field.read_only || envLocked
  const [copied, setCopied] = useState(false)

  const copyValue = async () => {
    const text = String(value ?? '')
    if (!text) return
    await navigator.clipboard.writeText(text)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-1.5 py-3 border-b border-[var(--color-border-subtle)] last:border-0">
      <div className="flex items-start justify-between gap-2">
        <label htmlFor={field.id} className="text-sm font-medium text-[var(--color-text-primary)]">
          {field.label}
        </label>
        <div className="flex gap-1 shrink-0">
          {envLocked && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-accent-amber/15 text-accent-amber">
              {t('config.env_lock')}
            </span>
          )}
          {setInFile && !envLocked && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-accent-sky/15 text-accent-sky">
              {t('config.in_file')}
            </span>
          )}
        </div>
      </div>
      {field.description && (
        <p className="text-xs text-[var(--color-text-tertiary)] leading-relaxed">{field.description}</p>
      )}

      {field.type === 'bool' && (
        <label className="inline-flex items-center gap-2 cursor-pointer">
          <input
            id={field.id}
            type="checkbox"
            className="rounded border-[var(--color-input-border)]"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(e) => onChange(field.id, e.target.checked)}
          />
          <span className="text-xs text-[var(--color-text-secondary)]">
            {Boolean(value) ? t('config.on') : t('config.off')}
          </span>
        </label>
      )}

      {field.type === 'enum' && field.options && (
        <select
          id={field.id}
          className={inputClass}
          value={String(value ?? field.default ?? '')}
          disabled={disabled}
          onChange={(e) => onChange(field.id, e.target.value)}
        >
          {field.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      )}

      {field.type === 'int' && (
        <input
          id={field.id}
          type="number"
          className={inputClass}
          value={value === null || value === undefined ? '' : Number(value)}
          min={field.min_value ?? undefined}
          disabled={disabled}
          onChange={(e) => onChange(field.id, e.target.value === '' ? field.default : Number(e.target.value))}
        />
      )}

      {field.type === 'string' && (
        <div className={field.read_only ? 'flex gap-2 items-stretch' : undefined}>
          <input
            id={field.id}
            type="text"
            className={inputClass}
            value={String(value ?? '')}
            readOnly={field.read_only}
            disabled={disabled && !field.read_only}
            onChange={(e) => onChange(field.id, e.target.value)}
          />
          {field.read_only && (
            <button
              type="button"
              title={t('config.copy')}
              className="shrink-0 px-3 rounded-lg border border-[var(--color-input-border)] bg-[var(--color-bg-elevated)] text-text-secondary hover:bg-[var(--color-row-hover)] transition-colors"
              onClick={() => void copyValue()}
            >
              {copied ? <CheckIcon size={16} className="text-accent-emerald" /> : <CopyIcon size={16} />}
            </button>
          )}
        </div>
      )}

      {field.type === 'secret' && (
        <input
          id={field.id}
          type="password"
          className={inputClass}
          value={value === null || value === undefined ? '' : String(value)}
          placeholder={secretConfigured ? t('config.secret_placeholder') : ''}
          disabled={disabled}
          autoComplete="off"
          onChange={(e) => onChange(field.id, e.target.value === '' ? null : e.target.value)}
        />
      )}
    </div>
  )
}
