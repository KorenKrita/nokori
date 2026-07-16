import { describe, expect, it } from 'vitest'
import { localeKeySets, maintenanceJobLabel, statusLabel, t, setLocale } from './i18n'

describe('i18n key parity', () => {
  it('zh/en/ja share the same key set', () => {
    const sets = localeKeySets()
    const locales = Object.keys(sets) as Array<keyof typeof sets>
    const reference = sets.en
    for (const loc of locales) {
      const missing = [...reference].filter((k) => !sets[loc].has(k))
      const extra = [...sets[loc]].filter((k) => !reference.has(k))
      expect(missing, `${loc} missing keys`).toEqual([])
      expect(extra, `${loc} extra keys`).toEqual([])
    }
  })
})

describe('i18n helpers', () => {
  it('t interpolates params and falls back', () => {
    setLocale('en')
    expect(t('rules.total', { n: 3 })).toContain('3')
    expect(t('__missing_key__')).toBe('__missing_key__')
  })

  it('statusLabel and maintenanceJobLabel fall back to raw', () => {
    setLocale('en')
    expect(statusLabel('active')).not.toBe('active')
    expect(statusLabel('not-a-status')).toBe('not-a-status')
    expect(maintenanceJobLabel('not-a-job')).toBe('not-a-job')
  })
})
