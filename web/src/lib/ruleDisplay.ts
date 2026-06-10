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

export function triggerVariantText(variant: TriggerVariant): string {
  if (typeof variant === 'string') return variant
  const kindKey = variant.kind ? VARIANT_KIND_KEYS[variant.kind] : null
  const kind = kindKey ? `[${t(kindKey)}]` : ''
  const concepts = variant.requires_concepts?.length ? ` → ${variant.requires_concepts.join(',')}` : ''
  return `${kind} ${variant.text ?? ''}${concepts}`.trim()
}
