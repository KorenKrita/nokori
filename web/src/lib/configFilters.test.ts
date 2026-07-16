import { describe, expect, it } from 'vitest'
import {
  fieldMatchesQuery,
  fieldMatchesSetFilter,
  sectionHasVisibleContent,
  visibleFieldsInSection,
} from './configFilters'
import type { ConfigFieldSchema, ConfigSectionSchema } from './configTypes'

const field = (id: string, label = id): ConfigFieldSchema => ({
  id,
  label,
  description: `${label} desc`,
  type: 'string',
  default: null,
  options: null,
  min_value: null,
  read_only: false,
  exclusive_group: null,
  exclusive_variant: null,
})

const section = (fields: ConfigFieldSchema[]): ConfigSectionSchema => ({
  id: 'sec',
  label: 'Section',
  fields,
})

describe('configFilters', () => {
  it('fieldMatchesQuery matches id/label/description', () => {
    const f = field('gate.enabled', 'Gate Enabled')
    expect(fieldMatchesQuery(f, '')).toBe(true)
    expect(fieldMatchesQuery(f, 'gate')).toBe(true)
    expect(fieldMatchesQuery(f, 'ENABLED')).toBe(true)
    expect(fieldMatchesQuery(f, 'desc')).toBe(true)
    expect(fieldMatchesQuery(f, 'zzz')).toBe(false)
  })

  it('fieldMatchesSetFilter respects onlySetInFile', () => {
    const setKeys = new Set(['a'])
    expect(fieldMatchesSetFilter('a', true, setKeys)).toBe(true)
    expect(fieldMatchesSetFilter('b', true, setKeys)).toBe(false)
    expect(fieldMatchesSetFilter('b', false, setKeys)).toBe(true)
  })

  it('visibleFieldsInSection and sectionHasVisibleContent', () => {
    const sec = section([field('a', 'Alpha'), field('b', 'Beta')])
    const setKeys = new Set(['a'])
    expect(visibleFieldsInSection(sec, 'alpha', false, setKeys)).toHaveLength(1)
    expect(sectionHasVisibleContent(sec, 'zzz', false, setKeys)).toBe(false)
    expect(sectionHasVisibleContent(sec, '', true, setKeys)).toBe(true)
  })
})
