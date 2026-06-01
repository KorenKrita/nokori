import { useLocale } from '@/hooks/useLocale'
import type { Locale } from '@/lib/i18n'

const LOCALES: { value: Locale; label: string }[] = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'EN' },
  { value: 'ja', label: '日本語' },
]

export function LocaleSwitcher() {
  const { locale, setLocale } = useLocale()

  return (
    <div className="flex gap-0.5 rounded-full bg-[var(--color-pill-hover-bg)] p-0.5">
      {LOCALES.map(({ value, label }) => (
        <button
          key={value}
          onClick={() => { setLocale(value); window.location.href = window.location.pathname }}
          className={`px-2.5 py-1 rounded-full text-[10px] font-medium transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
            locale === value
              ? 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]'
              : 'text-text-tertiary hover:text-text-secondary'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
