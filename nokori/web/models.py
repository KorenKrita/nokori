from __future__ import annotations

from pydantic import BaseModel, Field


class Meta(BaseModel):
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Decision Features (fielded evidence for a retrieval decision)
# ---------------------------------------------------------------------------


class DecisionFeaturesOut(BaseModel):
    trigger_idf_sum: float
    trigger_coverage: float
    distinct_trigger_terms: int
    strong_variant_phrase_hit: bool
    weak_variant_recall_hit: bool
    required_concepts_match: bool
    excluded_context_hit: bool
    excluded_context_override_passed: bool = False
    action_only_match: bool
    search_only_match: bool
    embedding_only_match: bool
    embedding_cosine: float | None = None
    embedding_profile_bucket: str | None = None
    matched_trigger_tokens: list[str] = []
    matched_variant_tokens: list[str] = []
    decision_reason: str = ""


# ---------------------------------------------------------------------------
# Fire Event (rule injection into a session)
# ---------------------------------------------------------------------------


class FireEventOut(BaseModel):
    id: str
    rule_id: str
    session_id: str
    injected_rule_version: int | None = None
    injected_trigger_snapshot: str | None = None
    injected_action_snapshot: str | None = None
    injected_structured_snapshot: dict | None = None
    trigger_idf_pool_version: str | None = None
    runtime_policy_version: str | None = None
    embedding_profile_version: str | None = None
    prompt_hash: str | None = None
    turn_index: int | None = None
    level: str
    decision_features: DecisionFeaturesOut | None = None
    posthoc_label: str | None = None
    posthoc_reason_code: str | None = None
    posthoc_score: float | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Shadow Event (shadow evaluation of candidate/suppressed rules)
# ---------------------------------------------------------------------------


class ShadowEventOut(BaseModel):
    id: str
    rule_id: str
    session_id: str
    rule_version: int | None = None
    prompt_hash: str | None = None
    label: str | None = None
    counterfactual: str | None = None
    fingerprint: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Synthetic Eval Summary
# ---------------------------------------------------------------------------


class SyntheticEvalSummary(BaseModel):
    rule_id: str
    rule_version: int
    passed: bool
    runtime_policy_version: str | None = None
    tokenizer_version: str | None = None
    matcher_compiler_version: str | None = None
    benchmark_version: str | None = None
    total_cases: int = 0
    positive_passed: int = 0
    positive_total: int = 0
    near_miss_passed: int = 0
    near_miss_total: int = 0
    negative_passed: int = 0
    negative_total: int = 0
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Eligibility Result
# ---------------------------------------------------------------------------


class EligibilityOut(BaseModel):
    decision: str  # cold | warm | hot | gate
    eligible: bool
    reason: str
    trigger_evidence_passed: bool
    penalties: list[str] = []


# ---------------------------------------------------------------------------
# Rule Response (full structured rule for the flywheel)
# ---------------------------------------------------------------------------


class RuleResponse(BaseModel):
    # Identity
    id: str
    short_id: str
    schema_version: int
    rule_version: int

    # Versioning
    created_by_pipeline_version: str
    runtime_policy_version: str
    last_rewritten_by_role: str | None = None

    # Lifecycle
    status: str
    severity: str

    # Trigger (structured)
    trigger_canonical: str
    trigger_canonical_zh: str | None = None
    concepts: list[dict] = []
    required_concept_groups: list[dict] = []
    excluded_contexts: list[dict] = []
    near_miss_examples: list[str] = []
    trigger_variants: list[str | dict] = []
    trigger_variants_zh: list[str] = []
    search_terms: dict[str, list[str]] = {}

    # Action (structured)
    action_instruction: str = ""
    action_instruction_zh: str | None = None
    allowed_behavior: list[str] = []
    forbidden_behavior: list[str] = []

    # Scope
    domain_tags: list[str] = []
    tool_tags: list[str] = []
    path_patterns: list[str] = []

    # Evidence
    evidence_quotes: list[str] = []

    # Quality scores
    quality_score: float = 0.0
    evidence_support_score: float = 0.0
    specificity_score: float = 0.0
    retrieval_readiness_score: float = 0.0

    # Usefulness scores
    observed_usefulness_score: float = 0.0
    plausible_usefulness_score: float = 0.0
    false_positive_score: float = 0.0
    harmful_score: float = 0.0

    # Origin
    source_origin: str = "transcript_extraction"
    activation_origin: str | None = None
    first_observed_useful_at: str | None = None

    # State timestamps
    trusted_at: str | None = None
    suppressed_at: str | None = None

    # Project scope
    project_scope: str = "global"
    project_id: str | None = None

    # Archive / lineage
    archived_reason: str | None = None
    replacement_id: str | None = None

    # Timestamps
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# Legacy models (kept for backward compat on deprecated endpoints)
# ---------------------------------------------------------------------------




class RetrieveRequest(BaseModel):
    prompt: str = Field(max_length=20000)
    project_id: str | None = None
    use_embedding: bool = True


class InjectionOut(BaseModel):
    id: int
    rule_id: str
    rule_short_id: str | None = None
    rule_project_scope: str | None = None
    rule_project_id: str | None = None
    session_id: str
    prompt_hash: str
    level: str
    created_at: str
