import type { ConfigValues } from '@/lib/configTypes'

export const EMBED_REMOTE_FIELD_IDS = [
  'embed.base_url',
  'embed.model',
  'embed.api_key',
] as const

export const EMBED_LOCAL_FIELD_IDS = [
  'embed.server_auto_start',
  'embed.hook_timeout_seconds',
  'embed.server_idle_seconds',
] as const

export function oppositeEmbedFieldIds(mode: 'local' | 'remote'): readonly string[] {
  return mode === 'local' ? EMBED_REMOTE_FIELD_IDS : EMBED_LOCAL_FIELD_IDS
}

/** Reset fields belonging to the non-selected embed backend (UI state only). */
export function clearOppositeEmbedBranch(
  values: ConfigValues,
  defaults: ConfigValues,
  mode: 'local' | 'remote',
): ConfigValues {
  const next = { ...values }
  for (const id of oppositeEmbedFieldIds(mode)) {
    const d = defaults[id]
    if (d !== undefined) {
      next[id] = d
    } else if (id.includes('api_key')) {
      next[id] = null
    } else if (typeof next[id] === 'boolean') {
      next[id] = false
    } else if (typeof next[id] === 'number') {
      next[id] = 0
    } else {
      next[id] = ''
    }
  }
  return next
}
