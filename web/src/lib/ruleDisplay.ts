import type { Rule, TriggerVariant } from '@/lib/types'

export function ruleTrigger(rule: Rule): string {
  return rule.trigger_canonical ?? rule.trigger_text ?? ''
}

export function ruleTriggerZh(rule: Rule): string | null {
  return rule.trigger_canonical_zh ?? rule.trigger_text_zh ?? null
}

export function ruleAction(rule: Rule): string {
  return rule.action_instruction ?? rule.action ?? ''
}

export function ruleActionZh(rule: Rule): string | null {
  return rule.action_instruction_zh ?? rule.action_zh ?? null
}

export function ruleSource(rule: Rule): string {
  return rule.source_origin ?? rule.source_type ?? '-'
}

export function ruleHitCount(rule: Rule): number {
  return rule.hit_count ?? 0
}

export function triggerVariantText(variant: TriggerVariant): string {
  return typeof variant === 'string' ? variant : variant.text ?? ''
}
