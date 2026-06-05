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
  trigger_text?: string
  trigger_variants?: TriggerVariant[]
  trigger_variants_zh?: string[]
  search_terms?: Record<string, string[]>
  action?: string
  action_instruction?: string
  action_instruction_zh?: string | null
  source_type?: string
  source_origin?: string
  confidence?: string
  severity?: string
  status: string
  evidence_score?: number
  evidence_log?: Record<string, unknown>[]
  hit_count?: number
  last_hit?: string | null
  shadow_hit_count?: number
  promotion_evidence?: Record<string, unknown>[]
  project_scope: string
  project_id: string | null
  superseded_by: string | null
  archived_reason: string | null
  created_at: string
  updated_at: string
  trigger_text_zh?: string | null
  action_zh?: string | null
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
    decision_reason: string
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
