import type { ConfigFieldSchema, ConfigSectionSchema } from '@/lib/configTypes'

export function fieldMatchesQuery(field: ConfigFieldSchema, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return (
    field.id.toLowerCase().includes(q)
    || field.label.toLowerCase().includes(q)
    || (field.description?.toLowerCase().includes(q) ?? false)
  )
}

export function fieldMatchesSetFilter(fieldId: string, onlySetInFile: boolean, setKeys: Set<string>): boolean {
  if (!onlySetInFile) return true
  return setKeys.has(fieldId)
}

export function visibleFieldsInSection(
  section: ConfigSectionSchema,
  query: string,
  onlySetInFile: boolean,
  setKeys: Set<string>,
): ConfigFieldSchema[] {
  return section.fields.filter(
    (f) => fieldMatchesQuery(f, query) && fieldMatchesSetFilter(f.id, onlySetInFile, setKeys),
  )
}

export function visibleVariantFields(
  section: ConfigSectionSchema,
  query: string,
  onlySetInFile: boolean,
  setKeys: Set<string>,
): boolean {
  if (!section.exclusive) return false
  for (const variant of section.exclusive.variants) {
    for (const field of variant.fields) {
      if (fieldMatchesQuery(field, query) && fieldMatchesSetFilter(field.id, onlySetInFile, setKeys)) {
        return true
      }
    }
  }
  return false
}

export function sectionHasVisibleContent(
  section: ConfigSectionSchema,
  query: string,
  onlySetInFile: boolean,
  setKeys: Set<string>,
): boolean {
  if (visibleFieldsInSection(section, query, onlySetInFile, setKeys).length > 0) return true
  return visibleVariantFields(section, query, onlySetInFile, setKeys)
}

/** Sections collapsed by default on first load */
export const DEFAULT_COLLAPSED_SECTIONS = new Set(['gate', 'extract', 'llm', 'session'])
