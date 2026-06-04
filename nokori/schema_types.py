"""Frozen dataclass structures for the autonomous rule quality flywheel.

These types represent the JSON-backed shapes used across cold path, hot path,
and posthoc evaluation. All dataclasses are frozen (immutable). Serialization
helpers (to_dict / from_dict) use only the standard library.

Literal type aliases are defined inline to avoid coupling to policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Literal type aliases used locally
# ---------------------------------------------------------------------------

AliasStrength = Literal["strong", "weak"]

ConceptMatchMode = Literal["any_alias", "phrase", "all_terms", "regex", "tool_pattern"]

VariantKind = Literal["strong_anchor", "weak_recall"]

ExcludedContextMatchMode = Literal[
    "negative_context_detector", "phrase", "regex", "tool_pattern"
]

ExcludedContextScope = Literal[
    "global", "near_trigger_span", "tool_input_only", "prompt_only"
]

SyntheticCaseType = Literal["positive", "medium_positive", "near_miss", "negative"]

MergeRelationShape = Literal[
    "equivalent",
    "new_broader",
    "new_narrower",
    "overlap",
    "complementary",
    "contradiction",
    "obsolete",
    "unrelated",
    "split_required",
]

MergeSafety = Literal["safe", "unsafe", "uncertain"]

MergeQualityWinner = Literal["new", "existing", "both", "neither"]

MergeOperation = Literal[
    "merge_into_existing",
    "update_existing_fields",
    "replace_existing",
    "keep_both",
    "reject_new",
    "suppress_existing",
    "archive_existing",
    "split_required",
]

ExtractorSourceType = Literal[
    "correction", "preference", "solution", "anti_pattern"
]

ExtractorConfidence = Literal["high", "medium", "low"]

CounterfactualLikelihood = Literal["yes", "no", "unclear"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys with None values for cleaner JSON output."""
    return {k: v for k, v in d.items() if v is not None}


def _to_dict_recursive(obj: Any) -> Any:
    """Recursively convert dataclass instances and nested structures to dicts."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, list):
        return [_to_dict_recursive(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_dict_recursive(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# 1. ConceptAlias
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConceptAlias:
    text: str
    strength: AliasStrength
    requires_neighbor: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text, "strength": self.strength}
        if self.requires_neighbor is not None:
            d["requires_neighbor"] = list(self.requires_neighbor)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConceptAlias:
        return cls(
            text=data["text"],
            strength=data["strength"],
            requires_neighbor=data.get("requires_neighbor"),
        )


# ---------------------------------------------------------------------------
# 2. Concept
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Concept:
    id: str
    label: str
    aliases: tuple[ConceptAlias, ...]
    match_mode: ConceptMatchMode
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "aliases": [a.to_dict() for a in self.aliases],
            "match_mode": self.match_mode,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Concept:
        return cls(
            id=data["id"],
            label=data["label"],
            aliases=tuple(ConceptAlias.from_dict(a) for a in data["aliases"]),
            match_mode=data["match_mode"],
            required=data["required"],
        )


# ---------------------------------------------------------------------------
# 3. RequiredConceptGroup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequiredConceptGroup:
    id: str
    all_of: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "all_of": list(self.all_of)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequiredConceptGroup:
        return cls(id=data["id"], all_of=tuple(data["all_of"]))


# ---------------------------------------------------------------------------
# 4. Variant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    text: str
    kind: VariantKind
    requires_concepts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "requires_concepts": list(self.requires_concepts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Variant:
        return cls(
            text=data["text"],
            kind=data["kind"],
            requires_concepts=tuple(data["requires_concepts"]),
        )


# ---------------------------------------------------------------------------
# 5. ExcludedContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExcludedContext:
    id: str
    label: str
    patterns: tuple[str, ...]
    match_mode: ExcludedContextMatchMode
    scope: ExcludedContextScope
    window_tokens: int
    override_allowed: bool
    override_requires: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "patterns": list(self.patterns),
            "match_mode": self.match_mode,
            "scope": self.scope,
            "window_tokens": self.window_tokens,
            "override_allowed": self.override_allowed,
            "override_requires": list(self.override_requires),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExcludedContext:
        return cls(
            id=data["id"],
            label=data["label"],
            patterns=tuple(data["patterns"]),
            match_mode=data["match_mode"],
            scope=data["scope"],
            window_tokens=data["window_tokens"],
            override_allowed=data["override_allowed"],
            override_requires=tuple(data.get("override_requires", ())),
        )


# ---------------------------------------------------------------------------
# 6. RuleTrigger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleTrigger:
    canonical: str
    required_concept_groups: tuple[RequiredConceptGroup, ...]
    concepts: tuple[Concept, ...]
    excluded_contexts: tuple[ExcludedContext, ...]
    variants: tuple[Variant, ...]
    near_miss_examples: tuple[str, ...]
    search_terms: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "required_concept_groups": [g.to_dict() for g in self.required_concept_groups],
            "concepts": [c.to_dict() for c in self.concepts],
            "excluded_contexts": [e.to_dict() for e in self.excluded_contexts],
            "variants": [v.to_dict() for v in self.variants],
            "near_miss_examples": list(self.near_miss_examples),
            "search_terms": {k: list(v) for k, v in self.search_terms.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleTrigger:
        return cls(
            canonical=data["canonical"],
            required_concept_groups=tuple(
                RequiredConceptGroup.from_dict(g)
                for g in data.get("required_concept_groups", ())
            ),
            concepts=tuple(Concept.from_dict(c) for c in data.get("concepts", ())),
            excluded_contexts=tuple(
                ExcludedContext.from_dict(e) for e in data.get("excluded_contexts", ())
            ),
            variants=tuple(Variant.from_dict(v) for v in data.get("variants", ())),
            near_miss_examples=tuple(data.get("near_miss_examples", ())),
            search_terms={
                k: tuple(v) for k, v in data.get("search_terms", {}).items()
            },
        )


# ---------------------------------------------------------------------------
# 7. RuleAction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleAction:
    instruction: str
    severity: Literal["reminder", "high_risk", "gate_eligible"]
    allowed_behavior: tuple[str, ...]
    forbidden_behavior: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "severity": self.severity,
            "allowed_behavior": list(self.allowed_behavior),
            "forbidden_behavior": list(self.forbidden_behavior),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleAction:
        return cls(
            instruction=data["instruction"],
            severity=data["severity"],
            allowed_behavior=tuple(data.get("allowed_behavior", ())),
            forbidden_behavior=tuple(data.get("forbidden_behavior", ())),
        )


# ---------------------------------------------------------------------------
# 8. RuleScope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleScope:
    domain_tags: tuple[str, ...]
    tool_tags: tuple[str, ...]
    file_or_path_patterns: tuple[str, ...]
    language_hints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_tags": list(self.domain_tags),
            "tool_tags": list(self.tool_tags),
            "file_or_path_patterns": list(self.file_or_path_patterns),
            "language_hints": list(self.language_hints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleScope:
        return cls(
            domain_tags=tuple(data.get("domain_tags", ())),
            tool_tags=tuple(data.get("tool_tags", ())),
            file_or_path_patterns=tuple(data.get("file_or_path_patterns", ())),
            language_hints=tuple(data.get("language_hints", ())),
        )


# ---------------------------------------------------------------------------
# 9. RuleEvidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleEvidence:
    transcript_refs: tuple[str, ...]
    evidence_quotes: tuple[str, ...]
    non_generalization_boundaries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript_refs": list(self.transcript_refs),
            "evidence_quotes": list(self.evidence_quotes),
            "non_generalization_boundaries": list(self.non_generalization_boundaries),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleEvidence:
        return cls(
            transcript_refs=tuple(data.get("transcript_refs", ())),
            evidence_quotes=tuple(data.get("evidence_quotes", ())),
            non_generalization_boundaries=tuple(
                data.get("non_generalization_boundaries", ())
            ),
        )


# ---------------------------------------------------------------------------
# 10. SyntheticEvalCase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyntheticEvalCase:
    prompt: str
    case_type: SyntheticCaseType
    expected_min_decision: Optional[str] = None
    expected_max_decision: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"prompt": self.prompt, "case_type": self.case_type}
        if self.expected_min_decision is not None:
            d["expected_min_decision"] = self.expected_min_decision
        if self.expected_max_decision is not None:
            d["expected_max_decision"] = self.expected_max_decision
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticEvalCase:
        return cls(
            prompt=data["prompt"],
            case_type=data["case_type"],
            expected_min_decision=data.get("expected_min_decision"),
            expected_max_decision=data.get("expected_max_decision"),
        )


# ---------------------------------------------------------------------------
# 11. MergePlannerOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergePlannerOutput:
    relation_shape: MergeRelationShape
    new_rule_safety: MergeSafety
    operation_safety: MergeSafety
    quality_winner: MergeQualityWinner
    operation: MergeOperation
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_shape": self.relation_shape,
            "new_rule_safety": self.new_rule_safety,
            "operation_safety": self.operation_safety,
            "quality_winner": self.quality_winner,
            "operation": self.operation,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MergePlannerOutput:
        return cls(
            relation_shape=data["relation_shape"],
            new_rule_safety=data["new_rule_safety"],
            operation_safety=data["operation_safety"],
            quality_winner=data["quality_winner"],
            operation=data["operation"],
            confidence=data["confidence"],
            reason=data["reason"],
        )


# ---------------------------------------------------------------------------
# 12. PosthocOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PosthocOutput:
    label: Literal[
        "observed_useful", "plausible_useful", "irrelevant", "harmful", "unclear"
    ]
    reason_code: Literal[
        "useful_prevented_error",
        "useful_improved_quality",
        "useful_followed_preference",
        "irrelevant_not_applicable",
        "irrelevant_redundant",
        "irrelevant_unused",
        "harmful_distracted",
        "harmful_wrong_scope",
        "harmful_blocked_valid_action",
    ]
    rule_application_evidence: str
    would_likely_have_happened_without_rule: CounterfactualLikelihood

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "reason_code": self.reason_code,
            "rule_application_evidence": self.rule_application_evidence,
            "would_likely_have_happened_without_rule": self.would_likely_have_happened_without_rule,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PosthocOutput:
        return cls(
            label=data["label"],
            reason_code=data["reason_code"],
            rule_application_evidence=data["rule_application_evidence"],
            would_likely_have_happened_without_rule=data[
                "would_likely_have_happened_without_rule"
            ],
        )


# ---------------------------------------------------------------------------
# 13. ExtractorOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractorOutput:
    trigger_draft: str
    action_draft: str
    behavior_draft: str
    source_type: ExtractorSourceType
    confidence_guess: ExtractorConfidence
    evidence_quotes: tuple[str, ...]
    non_generalization_boundaries: tuple[str, ...]
    required_concepts_draft: tuple[str, ...]
    excluded_contexts_draft: tuple[str, ...]
    search_terms_draft: dict[str, tuple[str, ...]]
    trigger_variants_draft: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_draft": self.trigger_draft,
            "action_draft": self.action_draft,
            "behavior_draft": self.behavior_draft,
            "source_type": self.source_type,
            "confidence_guess": self.confidence_guess,
            "evidence_quotes": list(self.evidence_quotes),
            "non_generalization_boundaries": list(self.non_generalization_boundaries),
            "required_concepts_draft": list(self.required_concepts_draft),
            "excluded_contexts_draft": list(self.excluded_contexts_draft),
            "search_terms_draft": {
                k: list(v) for k, v in self.search_terms_draft.items()
            },
            "trigger_variants_draft": list(self.trigger_variants_draft),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractorOutput:
        return cls(
            trigger_draft=data["trigger_draft"],
            action_draft=data["action_draft"],
            behavior_draft=data["behavior_draft"],
            source_type=data["source_type"],
            confidence_guess=data["confidence_guess"],
            evidence_quotes=tuple(data.get("evidence_quotes", ())),
            non_generalization_boundaries=tuple(
                data.get("non_generalization_boundaries", ())
            ),
            required_concepts_draft=tuple(data.get("required_concepts_draft", ())),
            excluded_contexts_draft=tuple(data.get("excluded_contexts_draft", ())),
            search_terms_draft={
                k: tuple(v) for k, v in data.get("search_terms_draft", {}).items()
            },
            trigger_variants_draft=tuple(data.get("trigger_variants_draft", ())),
        )


# ---------------------------------------------------------------------------
# 14. AdmissionScores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdmissionScores:
    evidence_support: float
    trigger_specificity: float
    action_clarity: float
    scope_control: float
    generalization_safety: float
    retrieval_readiness: float
    overall_quality: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_support": self.evidence_support,
            "trigger_specificity": self.trigger_specificity,
            "action_clarity": self.action_clarity,
            "scope_control": self.scope_control,
            "generalization_safety": self.generalization_safety,
            "retrieval_readiness": self.retrieval_readiness,
            "overall_quality": self.overall_quality,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdmissionScores:
        return cls(
            evidence_support=data["evidence_support"],
            trigger_specificity=data["trigger_specificity"],
            action_clarity=data["action_clarity"],
            scope_control=data["scope_control"],
            generalization_safety=data["generalization_safety"],
            retrieval_readiness=data["retrieval_readiness"],
            overall_quality=data["overall_quality"],
        )


# ---------------------------------------------------------------------------
# 15. DecisionFeatures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionFeatures:
    trigger_idf_sum: float
    trigger_coverage: float
    distinct_trigger_terms: int
    strong_variant_phrase_hit: bool
    weak_variant_recall_hit: bool
    required_concepts_match: bool
    excluded_context_hit: bool
    action_only_match: bool
    search_only_match: bool
    embedding_only_match: bool
    embedding_cosine: Optional[float] = None
    embedding_profile_bucket: Optional[str] = None
    matched_trigger_tokens: tuple[str, ...] = ()
    matched_variant_tokens: tuple[str, ...] = ()
    decision_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "trigger_idf_sum": self.trigger_idf_sum,
            "trigger_coverage": self.trigger_coverage,
            "distinct_trigger_terms": self.distinct_trigger_terms,
            "strong_variant_phrase_hit": self.strong_variant_phrase_hit,
            "weak_variant_recall_hit": self.weak_variant_recall_hit,
            "required_concepts_match": self.required_concepts_match,
            "excluded_context_hit": self.excluded_context_hit,
            "action_only_match": self.action_only_match,
            "search_only_match": self.search_only_match,
            "embedding_only_match": self.embedding_only_match,
            "matched_trigger_tokens": list(self.matched_trigger_tokens),
            "matched_variant_tokens": list(self.matched_variant_tokens),
            "decision_reason": self.decision_reason,
        }
        if self.embedding_cosine is not None:
            d["embedding_cosine"] = self.embedding_cosine
        if self.embedding_profile_bucket is not None:
            d["embedding_profile_bucket"] = self.embedding_profile_bucket
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionFeatures:
        return cls(
            trigger_idf_sum=data["trigger_idf_sum"],
            trigger_coverage=data["trigger_coverage"],
            distinct_trigger_terms=data["distinct_trigger_terms"],
            strong_variant_phrase_hit=data["strong_variant_phrase_hit"],
            weak_variant_recall_hit=data["weak_variant_recall_hit"],
            required_concepts_match=data["required_concepts_match"],
            excluded_context_hit=data["excluded_context_hit"],
            action_only_match=data["action_only_match"],
            search_only_match=data["search_only_match"],
            embedding_only_match=data["embedding_only_match"],
            embedding_cosine=data.get("embedding_cosine"),
            embedding_profile_bucket=data.get("embedding_profile_bucket"),
            matched_trigger_tokens=tuple(data.get("matched_trigger_tokens", ())),
            matched_variant_tokens=tuple(data.get("matched_variant_tokens", ())),
            decision_reason=data.get("decision_reason", ""),
        )


# ---------------------------------------------------------------------------
# 16. ArchivedFingerprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchivedFingerprint:
    signature: str
    scope_summary: str
    blocked_trigger_area: str
    blocked_action_area: str
    archive_strength: Literal["user", "system", "replacement"]
    can_be_overridden_by_changed_scope: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "scope_summary": self.scope_summary,
            "blocked_trigger_area": self.blocked_trigger_area,
            "blocked_action_area": self.blocked_action_area,
            "archive_strength": self.archive_strength,
            "can_be_overridden_by_changed_scope": self.can_be_overridden_by_changed_scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchivedFingerprint:
        return cls(
            signature=data["signature"],
            scope_summary=data["scope_summary"],
            blocked_trigger_area=data["blocked_trigger_area"],
            blocked_action_area=data["blocked_action_area"],
            archive_strength=data["archive_strength"],
            can_be_overridden_by_changed_scope=data["can_be_overridden_by_changed_scope"],
        )
