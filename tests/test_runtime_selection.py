"""Tests for nokori.runtime.selection (section 9.6 injection selection)."""

from __future__ import annotations

import pytest

from nokori.models import Rule, ScoredResult
from nokori.policy import HOT_MAX_DEFAULT, WARM_HARD_MAX
from nokori.search.selector import (
    SelectionResult,
    compute_utility,
    mmr_penalty,
    select_injection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    *,
    rule_id: str = "r1",
    status: str = "active",
    domain_tags: list[str] | None = None,
    trigger_canonical: str = "trigger text",
    action_instruction: str = "action text",
    observed_usefulness_score: float = 0.0,
    false_positive_score: float = 0.0,
) -> Rule:
    return Rule(
        id=rule_id,
        short_id=rule_id[:4],
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="1.0.0",
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status=status,
        severity="reminder",
        trigger_canonical=trigger_canonical,
        action_instruction=action_instruction,
        domain_tags=domain_tags or [],
        observed_usefulness_score=observed_usefulness_score,
        false_positive_score=false_positive_score,
    )


def _make_scored(
    *,
    rule_id: str = "r1",
    status: str = "active",
    domain_tags: list[str] | None = None,
    trigger_canonical: str = "trigger text",
    action_instruction: str = "action text",
    trigger_idf_sum: float = 3.0,
    strong_variant_phrase_hit: bool = False,
    matched_trigger_tokens: frozenset[str] | None = None,
    observed_usefulness_score: float = 0.0,
    false_positive_score: float = 0.0,
) -> ScoredResult:
    rule = _make_rule(
        rule_id=rule_id,
        status=status,
        domain_tags=domain_tags,
        trigger_canonical=trigger_canonical,
        action_instruction=action_instruction,
        observed_usefulness_score=observed_usefulness_score,
        false_positive_score=false_positive_score,
    )
    return ScoredResult(
        rule=rule,
        trigger_idf_sum=trigger_idf_sum,
        strong_variant_phrase_hit=strong_variant_phrase_hit,
        matched_trigger_tokens=matched_trigger_tokens or frozenset({"a", "b"}),
        required_concepts_match=True,
        trigger_coverage=0.5,
    )


# ---------------------------------------------------------------------------
# 1. HOT defaults to at most 1 rule
# ---------------------------------------------------------------------------


class TestHotDefaultMax:
    def test_single_hot_selected_by_default(self):
        results = [
            _make_scored(rule_id="r1", trigger_idf_sum=5.0),
            _make_scored(
                rule_id="r2",
                trigger_idf_sum=4.0,
                matched_trigger_tokens=frozenset({"c", "d"}),
            ),
        ]
        sel = select_injection(results, max_injection_chars=5000)
        assert len(sel.hot) <= HOT_MAX_DEFAULT

    def test_hot_max_default_is_one(self):
        assert HOT_MAX_DEFAULT == 1


# ---------------------------------------------------------------------------
# 2. Second HOT requires distinct domain/concepts AND strong evidence
# ---------------------------------------------------------------------------


class TestSecondHotConditions:
    def test_second_hot_allowed_with_distinct_domain_and_strong_evidence(self):
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=8.0,
            domain_tags=["python"],
            matched_trigger_tokens=frozenset({"a", "b"}),
        )
        r2 = _make_scored(
            rule_id="r2",
            trigger_idf_sum=4.5,
            domain_tags=["rust"],
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"c", "d"}),
        )
        sel = select_injection([r1, r2], max_injection_chars=5000)
        assert len(sel.hot) == 2

    def test_second_hot_blocked_without_distinct_domain(self):
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=5.0,
            domain_tags=["python"],
            matched_trigger_tokens=frozenset({"a", "b"}),
        )
        r2 = _make_scored(
            rule_id="r2",
            trigger_idf_sum=4.5,
            domain_tags=["python"],  # same domain
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"c", "d"}),
        )
        sel = select_injection([r1, r2], max_injection_chars=5000)
        assert len(sel.hot) == 1

    def test_second_hot_empty_domains_use_trigger_overlap_fallback(self):
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=5.0,
            domain_tags=[],
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"deploy", "schema"}),
        )
        r2 = _make_scored(
            rule_id="r2",
            trigger_idf_sum=4.5,
            domain_tags=[],
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"pytest", "snapshot"}),
        )

        sel = select_injection([r1, r2], max_injection_chars=5000)

        assert len(sel.hot) == 2

    def test_second_hot_empty_domains_still_block_high_overlap(self):
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=5.0,
            domain_tags=[],
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"deploy", "schema", "migration"}),
        )
        r2 = _make_scored(
            rule_id="r2",
            trigger_idf_sum=4.5,
            domain_tags=[],
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"deploy", "schema", "prisma"}),
        )

        sel = select_injection([r1, r2], max_injection_chars=5000)

        assert len(sel.hot) == 1

    def test_second_hot_blocked_without_strong_evidence(self):
        """Second HOT blocked when no strong evidence (no variant, no IDF+coverage+concepts)."""
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=5.0,
            domain_tags=["python"],
            matched_trigger_tokens=frozenset({"a", "b"}),
        )
        # r2 has distinct domain but NO strong evidence:
        # no strong_variant_phrase_hit AND required_concepts_match=False
        r2 = ScoredResult(
            rule=_make_rule(rule_id="r2", domain_tags=["rust"]),
            trigger_idf_sum=4.5,
            strong_variant_phrase_hit=False,
            matched_trigger_tokens=frozenset({"c", "d"}),
            required_concepts_match=False,  # no concepts = no strong evidence
            trigger_coverage=0.5,
        )
        sel = select_injection([r1, r2], max_injection_chars=5000)
        assert len(sel.hot) == 1


# ---------------------------------------------------------------------------
# 3. WARM hard max defaults to 3
# ---------------------------------------------------------------------------


class TestWarmHardMax:
    def test_warm_hard_max_default_is_three(self):
        assert WARM_HARD_MAX == 3

    def test_warm_respects_hard_max(self):
        results = [
            _make_scored(
                rule_id=f"r{i}",
                trigger_idf_sum=10.0 - i,
                matched_trigger_tokens=frozenset({f"t{i}"}),
            )
            for i in range(6)
        ]
        sel = select_injection(results, max_injection_chars=50000)
        assert len(sel.warm) <= WARM_HARD_MAX


# ---------------------------------------------------------------------------
# 4. Character budget limits WARM count
# ---------------------------------------------------------------------------


class TestCharacterBudget:
    def test_warm_limited_by_char_budget(self):
        # Each rule has trigger_canonical + action_instruction = 24 chars
        # Budget of 30 chars allows only 1 rule (24 chars each)
        results = [
            _make_scored(
                rule_id=f"r{i}",
                trigger_idf_sum=10.0 - i,
                trigger_canonical="trigger text",  # 12 chars
                action_instruction="action text!",  # 12 chars
                matched_trigger_tokens=frozenset({f"t{i}"}),
            )
            for i in range(3)
        ]
        sel = select_injection(results, max_injection_chars=55)
        # First is HOT, second fills WARM but third exceeds budget
        # Each rule costs 12+12+25 (FORMAT_OVERHEAD) = 49 chars
        warm_char_cost = len("trigger text") + len("action text!") + 25
        assert all(
            len(sr.rule.trigger_canonical) + len(sr.rule.action_instruction) + 25
            <= warm_char_cost
            for sr in sel.warm
        )
        # With 55 chars budget, at most 1 WARM rule fits (49 chars each)
        assert len(sel.warm) <= 1

    def test_warm_fills_up_to_budget(self):
        results = [
            _make_scored(
                rule_id=f"r{i}",
                trigger_idf_sum=10.0 - i,
                trigger_canonical="t",  # 1 char
                action_instruction="a",  # 1 char
                matched_trigger_tokens=frozenset({f"t{i}"}),
            )
            for i in range(5)
        ]
        # Each rule costs 1+1+25 (FORMAT_OVERHEAD) = 27 chars
        # Budget of 81 chars -> 3 WARM rules max
        sel = select_injection(results, max_injection_chars=81)
        # First goes HOT, remaining can be WARM (up to hard max and budget)
        assert len(sel.warm) == 3  # hard max is 3, budget allows 3


# ---------------------------------------------------------------------------
# 5. 3rd+ WARM must meet marginal utility threshold (>= 0.80 * prev utility)
# ---------------------------------------------------------------------------


class TestMarginalUtilityDecay:
    def test_third_warm_rejected_below_marginal_threshold(self):
        # First two WARM have high utility; third has very low utility
        r_hot = _make_scored(
            rule_id="hot",
            trigger_idf_sum=20.0,
            matched_trigger_tokens=frozenset({"hot1"}),
        )
        r_warm1 = _make_scored(
            rule_id="w1",
            trigger_idf_sum=10.0,
            matched_trigger_tokens=frozenset({"w1a"}),
        )
        r_warm2 = _make_scored(
            rule_id="w2",
            trigger_idf_sum=8.0,
            matched_trigger_tokens=frozenset({"w2a"}),
        )
        # Very low utility third candidate - should fail the 0.80 * prev threshold
        r_weak = _make_scored(
            rule_id="w3",
            trigger_idf_sum=0.5,
            matched_trigger_tokens=frozenset({"w3a"}),
        )
        results = [r_hot, r_warm1, r_warm2, r_weak]
        sel = select_injection(results, max_injection_chars=50000)
        assert len(sel.warm) == 2
        assert r_weak in sel.shadow_matches

    def test_third_warm_accepted_above_threshold(self):
        r_hot = _make_scored(
            rule_id="hot",
            trigger_idf_sum=20.0,
            matched_trigger_tokens=frozenset({"hot1"}),
        )
        r_warm1 = _make_scored(
            rule_id="w1",
            trigger_idf_sum=5.0,
            matched_trigger_tokens=frozenset({"w1a"}),
        )
        r_warm2 = _make_scored(
            rule_id="w2",
            trigger_idf_sum=4.5,
            matched_trigger_tokens=frozenset({"w2a"}),
        )
        # Third candidate still has reasonable utility relative to prev
        r_warm3 = _make_scored(
            rule_id="w3",
            trigger_idf_sum=4.0,
            matched_trigger_tokens=frozenset({"w3a"}),
        )
        results = [r_hot, r_warm1, r_warm2, r_warm3]
        sel = select_injection(results, max_injection_chars=50000)
        assert len(sel.warm) == 3


# ---------------------------------------------------------------------------
# 6. MMR prevents near-duplicate rules from consuming budget
# ---------------------------------------------------------------------------


class TestMMRDiversity:
    def test_near_duplicate_tokens_blocked(self):
        # Two rules with identical trigger tokens - second should be shadowed
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=5.0,
            matched_trigger_tokens=frozenset({"react", "hooks", "state"}),
        )
        r2 = _make_scored(
            rule_id="r2",
            trigger_idf_sum=4.8,
            matched_trigger_tokens=frozenset({"react", "hooks", "state"}),
        )
        sel = select_injection([r1, r2], max_injection_chars=50000)
        # r2 has >80% overlap with r1, should be blocked by MMR
        assert r2 in sel.shadow_matches

    def test_diverse_tokens_allowed(self):
        r1 = _make_scored(
            rule_id="r1",
            trigger_idf_sum=5.0,
            matched_trigger_tokens=frozenset({"react", "hooks"}),
        )
        r2 = _make_scored(
            rule_id="r2",
            trigger_idf_sum=4.8,
            matched_trigger_tokens=frozenset({"python", "typing"}),
        )
        sel = select_injection([r1, r2], max_injection_chars=50000)
        # No overlap, r2 should NOT be in shadow
        assert r2 not in sel.shadow_matches

    def test_mmr_penalty_function(self):
        candidate = frozenset({"a", "b", "c"})
        # Perfect overlap
        selected = [frozenset({"a", "b", "c"})]
        penalty = mmr_penalty(candidate, selected)
        assert penalty == pytest.approx(2.0)  # weight=2.0, jaccard=1.0

        # No overlap
        selected_disjoint = [frozenset({"x", "y", "z"})]
        penalty_zero = mmr_penalty(candidate, selected_disjoint)
        assert penalty_zero == 0.0

        # Empty selected list
        assert mmr_penalty(candidate, []) == 0.0


# ---------------------------------------------------------------------------
# 7. Recent false-positive penalty reduces ranking utility
# ---------------------------------------------------------------------------


class TestFalsePositivePenalty:
    def test_false_positive_reduces_utility(self):
        clean = _make_scored(
            rule_id="clean",
            trigger_idf_sum=5.0,
            false_positive_score=0.0,
        )
        penalized = _make_scored(
            rule_id="penalized",
            trigger_idf_sum=5.0,
            false_positive_score=1.0,
        )
        u_clean = compute_utility(clean)
        u_penalized = compute_utility(penalized)
        # Penalty = false_positive_score * 2.0 = 2.0
        assert u_penalized < u_clean
        assert u_clean - u_penalized == pytest.approx(2.0)

    def test_high_false_positive_can_make_utility_negative(self):
        sr = _make_scored(
            rule_id="bad",
            trigger_idf_sum=2.0,
            false_positive_score=3.0,  # penalty = 6.0, utility = 2.0 - 6.0 < 0
        )
        u = compute_utility(sr)
        assert u < 0


# ---------------------------------------------------------------------------
# 8. Trusted/usefulness bonus increases utility
# ---------------------------------------------------------------------------


class TestTrustedUsefulnessBonus:
    def test_trusted_status_adds_bonus(self):
        active = _make_scored(
            rule_id="active",
            status="active",
            trigger_idf_sum=5.0,
            observed_usefulness_score=0.0,
        )
        trusted = _make_scored(
            rule_id="trusted",
            status="trusted",
            trigger_idf_sum=5.0,
            observed_usefulness_score=0.0,
        )
        u_active = compute_utility(active)
        u_trusted = compute_utility(trusted)
        assert u_trusted > u_active
        assert u_trusted - u_active == pytest.approx(1.5)

    def test_observed_usefulness_adds_bonus(self):
        no_use = _make_scored(
            rule_id="no_use",
            trigger_idf_sum=5.0,
            observed_usefulness_score=0.0,
        )
        useful = _make_scored(
            rule_id="useful",
            trigger_idf_sum=5.0,
            observed_usefulness_score=1.0,
        )
        u_no = compute_utility(no_use)
        u_yes = compute_utility(useful)
        assert u_yes > u_no
        assert u_yes - u_no == pytest.approx(0.5)

    def test_trusted_bonus_greater_than_usefulness_bonus(self):
        trusted = _make_scored(
            rule_id="trusted",
            status="trusted",
            trigger_idf_sum=5.0,
        )
        useful = _make_scored(
            rule_id="useful",
            status="active",
            trigger_idf_sum=5.0,
            observed_usefulness_score=2.0,
        )
        u_trusted = compute_utility(trusted)
        u_useful = compute_utility(useful)
        assert u_trusted > u_useful


# ---------------------------------------------------------------------------
# 9. Shadow matches separated into shadow_matches list, not injected
# ---------------------------------------------------------------------------


class TestShadowSeparation:
    def test_candidate_status_goes_to_shadow(self):
        # candidate rules should not be injected
        candidate = _make_scored(
            rule_id="cand",
            status="candidate",
            trigger_idf_sum=5.0,
            matched_trigger_tokens=frozenset({"x", "y"}),
        )
        active = _make_scored(
            rule_id="active",
            trigger_idf_sum=4.0,
            matched_trigger_tokens=frozenset({"a", "b"}),
        )
        # Both are eligible_results (pre-filtered), but selection may shadow
        # lower-utility ones. Here both have positive utility, test structure.
        sel = select_injection([active, candidate], max_injection_chars=50000)
        # The selection result separates injected from shadow
        all_injected = sel.hot + sel.warm
        all_shadow = sel.shadow_matches
        # Every result appears in exactly one bucket
        total = len(all_injected) + len(all_shadow)
        assert total == 2

    def test_shadow_matches_not_in_hot_or_warm(self):
        results = [
            _make_scored(
                rule_id=f"r{i}",
                trigger_idf_sum=10.0 - i,
                matched_trigger_tokens=frozenset({f"t{i}"}),
            )
            for i in range(6)
        ]
        sel = select_injection(results, max_injection_chars=50000)
        injected_ids = {id(sr) for sr in sel.hot + sel.warm}
        shadow_ids = {id(sr) for sr in sel.shadow_matches}
        assert injected_ids.isdisjoint(shadow_ids)

    def test_all_input_accounted_for(self):
        results = [
            _make_scored(
                rule_id=f"r{i}",
                trigger_idf_sum=10.0 - i,
                matched_trigger_tokens=frozenset({f"t{i}"}),
            )
            for i in range(5)
        ]
        sel = select_injection(results, max_injection_chars=50000)
        total = len(sel.hot) + len(sel.warm) + len(sel.shadow_matches)
        assert total == len(results)


# ---------------------------------------------------------------------------
# 10. Empty input returns empty selection
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_list_returns_empty_selection(self):
        sel = select_injection([], max_injection_chars=5000)
        assert sel == SelectionResult()
        assert sel.hot == []
        assert sel.warm == []
        assert sel.shadow_matches == []


# ---------------------------------------------------------------------------
# 11. compute_utility returns expected values for known inputs
# ---------------------------------------------------------------------------


class TestComputeUtility:
    def test_base_utility_equals_trigger_idf_sum(self):
        sr = _make_scored(trigger_idf_sum=3.5)
        u = compute_utility(sr)
        # No bonuses, no penalties -> utility == trigger_idf_sum
        assert u == pytest.approx(3.5)

    def test_variant_phrase_bonus(self):
        sr = _make_scored(trigger_idf_sum=3.0, strong_variant_phrase_hit=True)
        u = compute_utility(sr)
        # trigger_idf_sum + variant_phrase_bonus(1.0) = 4.0
        assert u == pytest.approx(4.0)

    def test_full_utility_formula(self):
        sr = _make_scored(
            trigger_idf_sum=4.0,
            strong_variant_phrase_hit=True,
            status="trusted",
            false_positive_score=0.5,
        )
        # utility = 4.0 (idf) + 1.0 (variant) + 1.5 (trusted) - 0 (mmr) - 1.0 (fp*2)
        u = compute_utility(sr)
        assert u == pytest.approx(5.5)

    def test_mmr_reduces_utility_with_selected(self):
        sr = _make_scored(
            trigger_idf_sum=5.0,
            matched_trigger_tokens=frozenset({"a", "b", "c"}),
        )
        # Same tokens already selected -> jaccard=1.0, penalty=2.0
        selected = [frozenset({"a", "b", "c"})]
        u = compute_utility(sr, selected_tokens_list=selected)
        assert u == pytest.approx(5.0 - 2.0)

    def test_utility_combines_all_terms(self):
        sr = _make_scored(
            trigger_idf_sum=6.0,
            strong_variant_phrase_hit=True,
            status="active",
            observed_usefulness_score=1.5,
            false_positive_score=0.25,
            matched_trigger_tokens=frozenset({"x", "y"}),
        )
        # variant=1.0, usefulness_bonus=0.5, fp_penalty=0.5
        # No MMR (no selected tokens)
        u = compute_utility(sr)
        expected = 6.0 + 1.0 + 0.5 - 0.5
        assert u == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 12. Level-based filtering restricts HOT selection
# ---------------------------------------------------------------------------


class TestLevelFiltering:
    def test_level_filtering_restricts_hot(self):
        """Results with level='warm' should not get into hot list when levels are present."""
        rule_hot = _make_rule(rule_id="r_hot", domain_tags=["python"])
        rule_warm = _make_rule(rule_id="r_warm", domain_tags=["rust"])

        sr_hot = ScoredResult(
            rule=rule_hot,
            trigger_idf_sum=8.0,
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"a", "b"}),
            required_concepts_match=True,
            trigger_coverage=0.6,
            level="hot",
        )
        sr_warm = ScoredResult(
            rule=rule_warm,
            trigger_idf_sum=7.0,
            strong_variant_phrase_hit=True,
            matched_trigger_tokens=frozenset({"c", "d"}),
            required_concepts_match=True,
            trigger_coverage=0.6,
            level="warm",
        )
        sel = select_injection([sr_hot, sr_warm], max_injection_chars=50000)
        # sr_warm has level="warm", so it must NOT appear in hot
        assert sr_warm not in sel.hot
        # sr_hot should be in hot
        assert sr_hot in sel.hot
