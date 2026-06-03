export interface Rule {
  id: string
  short_id: string
  trigger_text: string
  trigger_variants: string[]
  search_terms: Record<string, string[]>
  behavior: string | null
  action: string
  rationale: string | null
  source_type: string
  confidence: string
  status: string
  evidence_score: number
  evidence_log: Record<string, unknown>[]
  hit_count: number
  last_hit: string | null
  shadow_hit_count: number
  promotion_evidence: Record<string, unknown>[]
  project_scope: string
  project_id: string | null
  superseded_by: string | null
  archived_reason: string | null
  created_at: string
  updated_at: string
  trigger_text_zh: string | null
  behavior_zh: string | null
  action_zh: string | null
  rationale_zh: string | null
}

export interface ScoredResult {
  rule: Rule
  bm25_score: number
  cosine: number | null
  rrf_score: number
  matched_tokens: string[]
  has_trigger_variant_match: boolean
  retrieval_hot: boolean
}

export interface DashboardData {
  rules: {
    total: number
    active: number
    dormant: number
    candidate: number
    merged: number
    archived: number
    global: number
  }
  injections_24h: number
  injections_hot_24h: number
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
