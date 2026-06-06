export type TriggerVariant = string | {
  text?: string
  kind?: string
  requires_concepts?: string[]
}

export interface Rule {
  id: string
  short_id: string
  schema_version?: number
  rule_version?: number
  trigger_canonical?: string
  trigger_canonical_zh?: string | null
  trigger_variants?: TriggerVariant[]
  trigger_variants_zh?: string[]
  search_terms?: Record<string, string[]>
  action_instruction?: string
  action_instruction_zh?: string | null
  allowed_behavior?: string[]
  forbidden_behavior?: string[]
  severity?: string
  status: string
  source_origin?: string
  activation_origin?: string | null
  evidence_quotes?: string[]
  domain_tags?: string[]
  tool_tags?: string[]
  path_patterns?: string[]
  quality_score?: number
  evidence_support_score?: number
  specificity_score?: number
  retrieval_readiness_score?: number
  observed_usefulness_score?: number
  plausible_usefulness_score?: number
  false_positive_score?: number
  harmful_score?: number
  first_observed_useful_at?: string | null
  trusted_at?: string | null
  suppressed_at?: string | null
  project_scope: string
  project_id: string | null
  archived_reason: string | null
  replacement_id?: string | null
  created_at: string
  updated_at: string
}

export interface ScoredResult {
  rule: Rule
  bm25_score: number
  cosine: number | null
  rrf_score: number
  ranking_utility: number
  decision_reason: string
  decision_features: {
    trigger_idf_sum: number
    trigger_coverage: number
    distinct_trigger_terms: number
    strong_variant_phrase_hit: boolean
    weak_variant_recall_hit: boolean
    required_concepts_match: boolean
    excluded_context_hit: boolean
    excluded_context_override_passed: boolean
    action_only_match: boolean
    search_only_match: boolean
    embedding_only_match: boolean
    embedding_cosine?: number
    embedding_profile_bucket?: string
    matched_trigger_tokens: string[]
    matched_variant_tokens: string[]
    matched_action_tokens?: string[]
    matched_search_tokens?: string[]
    decision_reason: string // same as top-level decision_reason; included in snapshot for completeness
  }
  eligibility: {
    decision: string
    eligible: boolean
    reason: string
    trigger_evidence_passed: boolean
    penalties: string[]
  }
}

export interface DashboardData {
  rules: {
    total: number
    active: number
    trusted: number
    candidate: number
    suppressed: number
    archived: number
    global: number
  }
  fire_events_24h: number
  fire_events_hot_24h: number
  gate_enabled: boolean
  embed_server: { running: boolean; pid: number | null; idle_seconds: number }
  extract_pending: number
  extract_mode: string
  promotion_enabled: boolean
  hot_cache_enabled: boolean
}

export interface Injection {
  id: number
  rule_id: string
  rule_short_id: string | null
  rule_project_scope: string | null
  rule_project_id: string | null
  session_id: string
  prompt_hash: string
  level: string
  created_at: string
}

export interface Meta {
  total: number
  page: number
  per_page: number
}
