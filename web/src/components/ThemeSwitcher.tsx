import { useState } from 'react'
import { Sun, Moon, Desktop } from '@phosphor-icons/react'
import { getTheme, setTheme, type Theme } from '@/lib/theme'
import { t } from '@/lib/i18n'

const OPTIONS: { value: Theme; icon: typeof Sun; key: string }[] = [
  { value: 'light', icon: Sun, key: 'theme.light' },
  { value: 'dark', icon: Moon, key: 'theme.dark' },
  { value: 'system', icon: Desktop, key: 'theme.system' },
]

export function ThemeSwitcher() {
  const [current, setCurrent] = useState<Theme>(getTheme())

  const handleChange = (theme: Theme) => {
    setTheme(theme)
    setCurrent(theme)
  }

  return (
    <div className="flex gap-0.5 rounded-full bg-white/[0.04] dark:bg-white/[0.04] light:bg-black/[0.04] p-0.5">
      {OPTIONS.map(({ value, icon: Icon, key }) => (
        <button
          key={value}
          onClick={() => handleChange(value)}
          title={t(key)}
          className={`p-1.5 rounded-full transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
            current === value
              ? 'bg-white/10 dark:bg-white/10 light:bg-black/10 text-white dark:text-white light:text-black'
              : 'text-text-tertiary hover:text-text-secondary'
          }`}
        >
          <Icon size={14} weight="light" />
        </button>
      ))}
    </div>
  )
}
