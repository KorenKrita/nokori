import type { Rule, TriggerVariant } from '@/lib/types'

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

export function triggerVariantText(variant: TriggerVariant): string {
  if (typeof variant === 'string') return variant
  const kind = variant.kind ? `[${variant.kind}]` : ''
  const concepts = variant.requires_concepts?.length ? ` → ${variant.requires_concepts.join(',')}` : ''
  return `${kind} ${variant.text ?? ''}${concepts}`.trim()
}
