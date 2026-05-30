from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


SourceType = Literal["correction", "preference", "solution", "anti_pattern"]
Confidence = Literal["high", "medium"]
Status = Literal["candidate", "active", "merged", "archived", "dormant"]
ProjectScope = Literal["project", "global"]
InjectionLevel = Literal["hot", "warm"]
TurnRole = Literal["human", "assistant", "tool_use", "tool_result"]


@dataclass(frozen=True)
class Rule:
    """Persistent rule row.

    cross_project_hits counts shadow HOT events (per project:day deduped in
    promotion_evidence). Global promotion threshold uses distinct project_ids
    in promotion_evidence, not this counter alone.
    """

    id: str
    short_id: str
    trigger_text: str
    trigger_variants: list[str]
    search_terms: dict[str, list[str]]
    behavior: str | None
    action: str
    rationale: str | None
    source_type: SourceType
    confidence: Confidence
    status: Status
    evidence_score: int
    evidence_log: list[dict]
    hit_count: int
    last_hit: str | None
    cross_project_hits: int
    promotion_evidence: list[dict]
    project_scope: ProjectScope
    project_id: str | None
    superseded_by: str | None
    archived_reason: str | None
    created_at: str
    updated_at: str


@dataclass
class ScoredResult:
    rule: Rule
    bm25_score: float = 0.0
    cosine: float | None = None
    rrf_score: float = 0.0
    matched_tokens: set[str] = field(default_factory=set)
    has_trigger_variant_match: bool = False
    retrieval_hot: bool = False


@dataclass(frozen=True)
class Turn:
    role: TurnRole
    content: str
    tool_name: str | None = None
    input_summary: str = ""
    is_error: bool = False
    error_line: str = ""
