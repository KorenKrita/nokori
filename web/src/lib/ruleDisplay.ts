import type { Rule, TriggerVariant } from '@/lib/types'
import { t } from '@/lib/i18n'

export function ruleTrigger(rule: Rule): string {
  return rule.trigger_canonical ?? ''
}

export function ruleTriggerZh(rule: Rule): string | null {
  return rule.trigger_canonical_zh ?? null
}

export function ruleAction(rule: Rule): string {
  return rule.action_instruction ?? ''
}

export function ruleActionZh(rule: Rule): string | null {
  return rule.action_instruction_zh ?? null
}

export function ruleSource(rule: Rule): string {
  return rule.source_origin ?? '-'
}

const VARIANT_KIND_KEYS: Record<string, string> = {
  strong_anchor: 'rules.variant.strong_anchor',
  weak_recall: 'rules.variant.weak_recall',
}

const SOURCE_KEYS: Record<string, string> = {
  transcript_extraction: 'rules.source.transcript_extraction',
  manual: 'rules.source.manual',
  external_source_material: 'rules.source.external_source_material',
}

const POSTHOC_KEYS: Record<string, string> = {
  observed_useful: 'rules.posthoc.observed_useful',
  plausible_useful: 'rules.posthoc.plausible_useful',
  irrelevant: 'rules.posthoc.irrelevant',
  harmful: 'rules.posthoc.harmful',
  unclear: 'rules.posthoc.unclear',
}

const LEVEL_KEYS: Record<string, string> = {
  hot: 'rules.level.hot',
  warm: 'rules.level.warm',
  cold: 'rules.level.cold',
}

export function triggerVariantText(variant: TriggerVariant): string {
  if (typeof variant === 'string') return variant
  const kindKey = variant.kind ? VARIANT_KIND_KEYS[variant.kind] : null
  const kind = kindKey ? `[${t(kindKey)}]` : ''
  const concepts = variant.requires_concepts?.length
    ? ` (${t('rules.variant.requires')}: ${variant.requires_concepts.join(', ')})`
    : ''
  return `${kind} ${variant.text ?? ''}${concepts}`.trim()
}

export function localizeSource(source: string): string {
  const key = SOURCE_KEYS[source]
  return key ? t(key) : source
}

export function localizePosthocLabel(label: string): string {
  const key = POSTHOC_KEYS[label]
  return key ? t(key) : label
}

export function localizeLevel(level: string): string {
  const key = LEVEL_KEYS[level]
  return key ? t(key) : level
}
