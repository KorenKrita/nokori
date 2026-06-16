"""Tests for ranking_utility consolidation: evidence.py computes base utility, selector consumes it."""

from dataclasses import replace

import pytest

from nokori.models import Rule, ScoredResult
from nokori.search.evidence import evaluate_evidence
from nokori.search.idf_stats import IdfPoolStats, build_idf_stats
from nokori.search.selector import compute_utility, select_injection

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rule(
    rule_id: str = "r-001",
    *,
    status: str = "trusted",
    severity: str = "reminder",
    trigger: str = "deploy database migration",
    first_observed_useful_at: str | None = "2026-01-01T00:00:00Z",
    false_positive_score: float = 0.0,
    observed_usefulness_score: float = 0.8,
) -> Rule:
    return Rule(
        id=rule_id,
        short_id=rule_id[:6],
        schema_version=7,
        rule_version=1,
        created_by_pipeline_version="1.0.0",
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status=status,
        severity=severity,
        trigger_canonical=trigger,
        action_instruction="run migration check first",
        concepts=[
            {
                "id": "deploy",
                "label": "deploy",
                "aliases": [{"text": "deploy", "strength": "strong"}],
                "match_mode": "phrase",
                "required": True,
            }
        ],
        required_concept_groups=[{"id": "primary", "all_of": ["deploy"]}],
        trigger_variants=[
            {
                "text": "deploy database migration",
                "kind": "strong_anchor",
                "requires_concepts": ["deploy"],
            }
        ],
        search_terms={"en": ["deploy", "migration", "database"], "zh": []},
        source_origin="transcript_extraction",
        project_scope="global",
        first_observed_useful_at=first_observed_useful_at,
        false_positive_score=false_positive_score,
        observed_usefulness_score=observed_usefulness_score,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def _make_scored_result(rule: Rule | None = None, **overrides) -> ScoredResult:
    if rule is None:
        rule = _make_rule()
    defaults = {
        "rule": rule,
        "bm25_score": 5.0,
        "matched_trigger_tokens": frozenset({"deploy", "database", "migration"}),
        "matched_variant_tokens": frozenset({"deploy", "database"}),
    }
    defaults.update(overrides)
    return ScoredResult(**defaults)


def _build_idf_for_rules(*rules: Rule) -> IdfPoolStats:
    return build_idf_stats(rules)


# ---------------------------------------------------------------------------
# 1. evaluate_evidence populates ranking_utility
# ---------------------------------------------------------------------------


class TestEvidencePopulatesRankingUtility:
    """After evidence evaluation, ranking_utility must be non-zero for eligible rules."""

    def test_trusted_rule_with_strong_evidence_has_positive_utility(self):
        rule = _make_rule(status="trusted")
        sr = _make_scored_result(rule)
        idf_stats = _build_idf_for_rules(rule)

        result = evaluate_evidence(sr, "deploy database migration now", idf_stats=idf_stats)

        assert result is not None
        assert result.ranking_utility > 0.0, (
            "ranking_utility must be set by evaluate_evidence for eligible rules"
        )

    def test_active_rule_with_observed_useful_has_utility(self):
        rule = _make_rule(status="active", first_observed_useful_at="2026-01-01T00:00:00Z")
        sr = _make_scored_result(rule)
        idf_stats = _build_idf_for_rules(rule)

        result = evaluate_evidence(sr, "deploy database migration now", idf_stats=idf_stats)

        assert result is not None
        assert result.ranking_utility > 0.0

    def test_active_rule_without_observed_useful_has_lower_utility(self):
        rule_with = _make_rule(
            rule_id="with-useful", status="active",
            first_observed_useful_at="2026-01-01T00:00:00Z",
            observed_usefulness_score=0.8,
        )
        rule_without = _make_rule(
            rule_id="no-useful", status="active",
            first_observed_useful_at=None,
            observed_usefulness_score=0.0,
        )
        idf_stats = _build_idf_for_rules(rule_with, rule_without)

        result_with = evaluate_evidence(
            _make_scored_result(rule_with), "deploy database migration now",
            idf_stats=idf_stats,
        )
        result_without = evaluate_evidence(
            _make_scored_result(rule_without), "deploy database migration now",
            idf_stats=idf_stats,
        )

        assert result_with is not None and result_without is not None
        assert result_with.ranking_utility > result_without.ranking_utility

    def test_trusted_rule_has_higher_utility_than_active(self):
        rule_trusted = _make_rule(rule_id="trusted-1", status="trusted")
        rule_active = _make_rule(
            rule_id="active-1", status="active",
            first_observed_useful_at="2026-01-01T00:00:00Z",
            observed_usefulness_score=0.5,
        )
        idf_stats = _build_idf_for_rules(rule_trusted, rule_active)

        result_trusted = evaluate_evidence(
            _make_scored_result(rule_trusted), "deploy database migration now",
            idf_stats=idf_stats,
        )
        result_active = evaluate_evidence(
            _make_scored_result(rule_active), "deploy database migration now",
            idf_stats=idf_stats,
        )

        assert result_trusted is not None and result_active is not None
        assert result_trusted.ranking_utility > result_active.ranking_utility

    def test_false_positive_score_reduces_utility(self):
        rule_clean = _make_rule(rule_id="clean", false_positive_score=0.0)
        rule_fp = _make_rule(rule_id="fp", false_positive_score=0.5)
        idf_stats = _build_idf_for_rules(rule_clean, rule_fp)

        result_clean = evaluate_evidence(
            _make_scored_result(rule_clean), "deploy database migration now",
            idf_stats=idf_stats,
        )
        result_fp = evaluate_evidence(
            _make_scored_result(rule_fp), "deploy database migration now",
            idf_stats=idf_stats,
        )

        assert result_clean is not None and result_fp is not None
        assert result_clean.ranking_utility > result_fp.ranking_utility

    def test_ineligible_result_gets_zero_utility(self):
        rule = _make_rule(status="candidate")
        sr = _make_scored_result(rule)
        idf_stats = _build_idf_for_rules(rule)

        result = evaluate_evidence(sr, "deploy database migration now", idf_stats=idf_stats)

        assert result is not None, "candidate rule with trigger evidence should still return a result"
        assert result.ranking_utility == 0.0


# ---------------------------------------------------------------------------
# 2. compute_utility uses ranking_utility as base, adds only MMR penalty
# ---------------------------------------------------------------------------


class TestSelectorConsumesRankingUtility:
    """compute_utility should use the pre-computed ranking_utility as its base value."""

    def test_no_mmr_penalty_returns_ranking_utility(self):
        rule = _make_rule()
        sr = replace(
            _make_scored_result(rule),
            ranking_utility=4.5,
            trigger_evidence_passed=True,
            trigger_idf_sum=0.0,
            strong_variant_phrase_hit=False,
        )
        utility = compute_utility(sr, selected_tokens_list=None)
        assert utility == pytest.approx(4.5, abs=0.01), (
            "Without MMR penalty, compute_utility should return the pre-computed ranking_utility"
        )

    def test_mmr_penalty_reduces_utility(self):
        rule = _make_rule()
        tokens = frozenset({"deploy", "database", "migration"})
        sr = replace(
            _make_scored_result(rule),
            ranking_utility=4.5,
            matched_trigger_tokens=tokens,
        )
        utility_no_mmr = compute_utility(sr, selected_tokens_list=None)
        utility_with_mmr = compute_utility(sr, selected_tokens_list=[tokens])

        assert utility_with_mmr < utility_no_mmr

    def test_zero_ranking_utility_result_has_zero_or_negative_utility(self):
        rule = _make_rule(status="candidate")
        sr = replace(
            _make_scored_result(rule),
            ranking_utility=0.0,
            trigger_idf_sum=0.0,
            strong_variant_phrase_hit=False,
            trigger_evidence_passed=True,
        )
        utility = compute_utility(sr, selected_tokens_list=None)
        assert utility <= 0.0


# ---------------------------------------------------------------------------
# 3. select_injection respects ranking_utility ordering
# ---------------------------------------------------------------------------


class TestSelectionRespectsRankingUtility:
    """Selection should prefer results with higher ranking_utility."""

    def test_higher_ranking_utility_selected_hot_first(self):
        rule_a = _make_rule(rule_id="a", status="trusted")
        rule_b = _make_rule(rule_id="b", status="trusted")

        sr_a = replace(
            _make_scored_result(rule_a),
            ranking_utility=6.0,
            level="hot",
            trigger_evidence_passed=True,
            matched_trigger_tokens=frozenset({"deploy", "database"}),
        )
        sr_b = replace(
            _make_scored_result(rule_b),
            ranking_utility=3.0,
            level="hot",
            trigger_evidence_passed=True,
            matched_trigger_tokens=frozenset({"migration", "schema"}),
        )

        selection = select_injection([sr_b, sr_a], max_injection_chars=5000)
        assert len(selection.hot) >= 1
        assert selection.hot[0].rule.id == "a", (
            "Higher ranking_utility should be selected first"
        )


# ---------------------------------------------------------------------------
# 4. End-to-end: evaluate_evidence → select_injection consistency
# ---------------------------------------------------------------------------


class TestEndToEndUtilityConsistency:
    """The ranking_utility set by evidence evaluation should match what selector uses."""

    def test_utility_flows_from_evidence_to_selection(self):
        rules = [
            _make_rule(rule_id="high", status="trusted", trigger="deploy database migration",
                       false_positive_score=0.0),
            _make_rule(rule_id="low", status="trusted", trigger="deploy database migration",
                       false_positive_score=0.4),
        ]
        idf_stats = _build_idf_for_rules(*rules)
        prompt = "deploy database migration now"

        results = []
        for rule in rules:
            sr = _make_scored_result(rule)
            evaluated = evaluate_evidence(sr, prompt, idf_stats=idf_stats)
            if evaluated is not None:
                results.append(evaluated)

        assert len(results) == 2
        high_result = next(r for r in results if r.rule.id == "high")
        low_result = next(r for r in results if r.rule.id == "low")
        assert high_result.ranking_utility > low_result.ranking_utility

        selection = select_injection(results, max_injection_chars=5000)
        assert selection.hot, "Expected at least one hot result for trusted rule"
        assert selection.hot[0].rule.id == "high"
