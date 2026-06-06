from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


TurnRole = Literal["human", "assistant", "tool_use", "tool_result"]

Status = Literal["candidate", "active", "trusted", "suppressed", "archived"]
Severity = Literal["reminder", "high_risk", "gate_eligible"]
SourceOrigin = Literal["transcript_extraction", "external_source_material"]
ActivationOrigin = Literal[
    "cold_fast_lane",
    "shadow_promotion",
    "merge_replacement",
    "external_shadow_promotion",
]
InjectionLevel = Literal["hot", "warm", "gate"]
ProjectScope = Literal["project", "global"]


@dataclass(frozen=True)
class Rule:
    """Persistent rule row for the autonomous quality flywheel."""

    id: str
    short_id: str

    # Versioning
    schema_version: int
    rule_version: int
    created_by_pipeline_version: str | None
    runtime_policy_version: str | None
    last_rewritten_by_role: str | None

    # Lifecycle
    status: Status
    severity: Severity

    # Trigger
    trigger_canonical: str
    trigger_canonical_zh: str | None = None
    concepts: str = "[]"  # JSON list[str]
    concept_aliases: str = "[]"  # JSON list[str]
    required_concept_groups: str = "[]"  # JSON list[str]
    excluded_contexts: str = "[]"  # JSON list[str]
    non_generalization_boundaries: str = "[]"  # JSON list[str]
    near_miss_examples: list[str] = field(default_factory=list)
    trigger_variants: str = "[]"  # JSON list of Variant objects
    trigger_variants_zh: list[str] = field(default_factory=list)
    search_terms: dict[str, list[str]] = field(default_factory=dict)

    # Action
    action_instruction: str = ""
    action_instruction_zh: str | None = None
    allowed_behavior: list[str] = field(default_factory=list)
    forbidden_behavior: list[str] = field(default_factory=list)

    # Scope
    domain_tags: list[str] = field(default_factory=list)
    tool_tags: list[str] = field(default_factory=list)
    path_patterns: list[str] = field(default_factory=list)
    language_hints: str = "[]"  # JSON list[str]
    transcript_ref: str | None = None

    # Evidence
    evidence_quotes: list[str] = field(default_factory=list)

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
    source_origin: SourceOrigin = "transcript_extraction"
    activation_origin: ActivationOrigin | None = None
    first_observed_useful_at: str | None = None

    # State timestamps
    trusted_at: str | None = None
    suppressed_at: str | None = None

    # Project scope
    project_scope: ProjectScope = "global"
    project_id: str | None = None

    # Archive / lineage
    archived_reason: str | None = None
    replacement_id: str | None = None

    # Timestamps
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ScoredResult:
    """Fielded retrieval evidence for a matched rule."""

    rule: Rule

    # Core retrieval scores
    bm25_score: float = 0.0
    cosine: float | None = None
    rrf_score: float = 0.0

    # Trigger evidence primitives
    trigger_idf_sum: float = 0.0
    trigger_coverage: float = 0.0
    distinct_trigger_terms: int = 0

    # Variant evidence
    strong_variant_phrase_hit: bool = False
    weak_variant_recall_hit: bool = False

    # Concept / context evidence
    required_concepts_match: bool = False
    excluded_context_hit: bool = False
    excluded_context_override_passed: bool = False

    # Match source flags
    action_only_match: bool = False
    search_only_match: bool = False
    embedding_only_match: bool = False

    # Explanation metadata
    matched_trigger_tokens: frozenset[str] = field(default_factory=frozenset)
    matched_variant_tokens: frozenset[str] = field(default_factory=frozenset)
    matched_action_tokens: frozenset[str] = field(default_factory=frozenset)
    matched_search_tokens: frozenset[str] = field(default_factory=frozenset)
    embedding_profile_bucket: str | None = None
    embedding_profile_version: str | None = None
    embedding_profile_unknown: bool = False
    trigger_idf_pool_version: str | None = None
    runtime_policy_version: str | None = None

    # Ranking
    ranking_utility: float = 0.0
    decision_reason: str = ""
    trigger_evidence_passed: bool = False
    decision_penalties: tuple[str, ...] = ()
    level: InjectionLevel | None = None


@dataclass(frozen=True)
class Turn:
    role: TurnRole
    content: str
    tool_name: str | None = None
    input_summary: str = ""
    is_error: bool = False
    error_line: str = ""
