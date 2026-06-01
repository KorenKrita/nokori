import { useCallback, useState } from 'react'
import { getLocale, setLocale as setI18nLocale, t, type Locale } from '@/lib/i18n'

export function useLocale() {
  const [locale, setLocaleState] = useState<Locale>(getLocale())

  const changeLocale = useCallback((newLocale: Locale) => {
    setI18nLocale(newLocale)
    setLocaleState(newLocale)
  }, [])

  return { locale, setLocale: changeLocale, t }
}
