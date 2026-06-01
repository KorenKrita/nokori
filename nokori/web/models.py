from __future__ import annotations

from pydantic import BaseModel


class Meta(BaseModel):
    total: int
    page: int
    per_page: int


class RuleOut(BaseModel):
    id: str
    short_id: str
    trigger_text: str
    trigger_variants: list[str]
    search_terms: dict[str, list[str]]
    behavior: str | None
    action: str
    rationale: str | None
    source_type: str
    confidence: str
    status: str
    evidence_score: int
    evidence_log: list[dict]
    hit_count: int
    last_hit: str | None
    shadow_hit_count: int
    promotion_evidence: list[dict]
    project_scope: str
    project_id: str | None
    superseded_by: str | None
    archived_reason: str | None
    created_at: str
    updated_at: str


class RuleEdit(BaseModel):
    trigger_text: str | None = None
    action: str | None = None
    rationale: str | None = None
    confidence: str | None = None
    status: str | None = None
    trigger_variants: list[str] | None = None
    search_terms: dict[str, list[str]] | None = None


class RetrieveRequest(BaseModel):
    prompt: str
    project_id: str | None = None
    use_embedding: bool = True


class InjectionOut(BaseModel):
    id: int
    rule_id: str
    rule_short_id: str | None = None
    session_id: str
    prompt_hash: str
    level: str
    created_at: str
