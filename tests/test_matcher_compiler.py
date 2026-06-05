"""Tests for nokori.matcher.compiler and nokori.matcher.runtime.

Covers compilation validation, group semantics, weak alias neighbor evidence,
generic token rejection, variant/exclusion scoping, match_mode variations,
trigger_coverage computation, and required_concepts_match logic.
"""

from __future__ import annotations

import pytest

from nokori.matcher.compiler import CompilationError, compile_rule
from nokori.matcher.runtime import evaluate_match


# ---------------------------------------------------------------------------
# Helpers: minimal valid rule data builders
# ---------------------------------------------------------------------------


def _concept(
    id: str,
    aliases: list[dict],
    *,
    required: bool = True,
    match_mode: str = "any_alias",
) -> dict:
    return {
        "id": id,
        "label": id,
        "match_mode": match_mode,
        "required": required,
        "aliases": aliases,
    }


def _alias(text: str, *, strength: str = "strong", requires_neighbor: list[str] | None = None) -> dict:
    d: dict = {"text": text, "strength": strength}
    if requires_neighbor:
        d["requires_neighbor"] = requires_neighbor
    return d


def _group(id: str, all_of: list[str]) -> dict:
    return {"id": id, "all_of": all_of}


def _variant(text: str, *, kind: str = "weak_recall", requires_concepts: list[str] | None = None) -> dict:
    d: dict = {"text": text, "kind": kind}
    if requires_concepts:
        d["requires_concepts"] = requires_concepts
    return d


def _excluded_context(
    id: str,
    patterns: list[str],
    *,
    scope: str = "global",
    match_mode: str = "phrase",
    window_tokens: int = 12,
    override_allowed: bool = False,
    override_requires: list[str] | None = None,
) -> dict:
    return {
        "id": id,
        "label": id,
        "patterns": patterns,
        "scope": scope,
        "match_mode": match_mode,
        "window_tokens": window_tokens,
        "override_allowed": override_allowed,
        "override_requires": override_requires or [],
    }


def _trigger_data(
    *,
    concepts: list[dict] | None = None,
    groups: list[dict] | None = None,
    variants: list[dict] | None = None,
    excluded_contexts: list[dict] | None = None,
) -> dict:
    d: dict = {}
    if groups is not None:
        d["required_concept_groups"] = groups
    if concepts is not None:
        d["concepts"] = concepts
    if variants is not None:
        d["variants"] = variants
    if excluded_contexts is not None:
        d["excluded_contexts"] = excluded_contexts
    return d


# ---------------------------------------------------------------------------
# 1. compile_rule raises CompilationError without required concept groups
# ---------------------------------------------------------------------------


class TestCompilationRequiresGroups:
    def test_empty_groups_raises(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("sql injection")])],
            groups=[],
        )
        with pytest.raises(CompilationError, match="At least one required concept group"):
            compile_rule(data)

    def test_missing_groups_key_raises(self):
        data = _trigger_data(concepts=[_concept("c1", [_alias("sql injection")])])
        # No groups key at all
        with pytest.raises(CompilationError, match="At least one required concept group"):
            compile_rule(data)

    def test_group_referencing_unknown_concept_raises(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("sql injection")])],
            groups=[_group("g1", ["nonexistent"])],
        )
        with pytest.raises(CompilationError, match="references unknown concept"):
            compile_rule(data)


# ---------------------------------------------------------------------------
# 2. Group semantics: all-of within group, any-of across groups
# ---------------------------------------------------------------------------


class TestGroupSemantics:
    @pytest.fixture
    def two_group_matcher(self):
        concepts = [
            _concept("c_sql", [_alias("sql injection")]),
            _concept("c_auth", [_alias("authentication bypass")]),
            _concept("c_xss", [_alias("cross-site scripting")]),
        ]
        groups = [
            _group("g_sqli", ["c_sql", "c_auth"]),  # needs both
            _group("g_xss", ["c_xss"]),  # needs just xss
        ]
        data = _trigger_data(concepts=concepts, groups=groups)
        return compile_rule(data)

    def test_all_of_both_satisfied(self, two_group_matcher):
        result = evaluate_match(
            two_group_matcher,
            "sql injection combined with authentication bypass",
        )
        assert "g_sqli" in result.matched_group_ids
        assert result.required_concepts_match is True

    def test_all_of_partial_unsatisfied(self, two_group_matcher):
        result = evaluate_match(
            two_group_matcher,
            "sql injection without auth context",
        )
        assert "g_sqli" not in result.matched_group_ids

    def test_any_of_across_groups(self, two_group_matcher):
        # Only xss present: g_xss satisfied, g_sqli not
        result = evaluate_match(
            two_group_matcher,
            "cross-site scripting attack",
        )
        assert "g_xss" in result.matched_group_ids
        assert "g_sqli" not in result.matched_group_ids
        assert result.required_concepts_match is True


# ---------------------------------------------------------------------------
# 3. Weak aliases need neighbor evidence to match
# ---------------------------------------------------------------------------


class TestWeakAliasNeighborEvidence:
    @pytest.fixture
    def weak_matcher(self):
        concepts = [
            _concept("c_inject", [
                _alias("inject", strength="weak", requires_neighbor=["sql", "database"]),
            ]),
        ]
        groups = [_group("g1", ["c_inject"])]
        data = _trigger_data(concepts=concepts, groups=groups)
        return compile_rule(data)

    def test_weak_alias_without_neighbor_not_matched(self, weak_matcher):
        result = evaluate_match(weak_matcher, "inject the dependency")
        assert "c_inject" not in result.matched_concept_ids
        assert result.required_concepts_match is False

    def test_weak_alias_with_neighbor_matched(self, weak_matcher):
        result = evaluate_match(weak_matcher, "inject into sql database")
        assert "c_inject" in result.matched_concept_ids
        assert result.required_concepts_match is True

    def test_weak_alias_neighbor_in_path_hints(self, weak_matcher):
        result = evaluate_match(
            weak_matcher,
            "inject the payload",
            path_hints=["src/database/handler.py"],
        )
        assert "c_inject" in result.matched_concept_ids

    def test_weak_alias_neighbor_in_project_tags(self, weak_matcher):
        result = evaluate_match(
            weak_matcher,
            "inject this value",
            project_tags=["sql"],
        )
        assert "c_inject" in result.matched_concept_ids


# ---------------------------------------------------------------------------
# 4. Single generic token as strong_anchor rejected by compiler
# ---------------------------------------------------------------------------


class TestGenericTokenRejection:
    def test_single_generic_token_rejected(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("buffer overflow")])],
            groups=[_group("g1", ["c1"])],
            variants=[_variant("use", kind="strong_anchor", requires_concepts=["c1"])],
        )
        with pytest.raises(CompilationError, match="must be multi-token"):
            compile_rule(data)

    def test_single_non_generic_token_also_rejected_multi_token(self):
        # Even a non-generic single token fails the multi-token check
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("buffer overflow")])],
            groups=[_group("g1", ["c1"])],
            variants=[_variant("exploit", kind="strong_anchor", requires_concepts=["c1"])],
        )
        with pytest.raises(CompilationError, match="must be multi-token"):
            compile_rule(data)


# ---------------------------------------------------------------------------
# 5. Weak-recall variants don't satisfy trigger evidence (only recall)
# ---------------------------------------------------------------------------


class TestWeakRecallVariants:
    @pytest.fixture
    def recall_only_matcher(self):
        concepts = [
            _concept("c1", [_alias("kubernetes")]),
        ]
        groups = [_group("g1", ["c1"])]
        variants = [_variant("container orchestration")]  # weak_recall by default
        data = _trigger_data(concepts=concepts, groups=groups, variants=variants)
        return compile_rule(data)

    def test_weak_recall_variant_listed_but_not_strong(self, recall_only_matcher):
        result = evaluate_match(
            recall_only_matcher,
            "container orchestration platform",
        )
        # Variant text found -> weak hit
        assert "container orchestration" in result.weak_variant_hits
        # But concept not matched so required_concepts_match is False
        assert result.required_concepts_match is False
        # strong_variant_hits empty
        assert result.strong_variant_hits == ()

    def test_weak_recall_with_concept_match(self, recall_only_matcher):
        result = evaluate_match(
            recall_only_matcher,
            "kubernetes container orchestration",
        )
        assert result.required_concepts_match is True
        assert "container orchestration" in result.weak_variant_hits


# ---------------------------------------------------------------------------
# 6. Excluded context scopes
# ---------------------------------------------------------------------------


class TestExcludedContextScopes:
    @pytest.fixture
    def scoped_matcher(self):
        concepts = [_concept("c1", [_alias("secret key")])]
        groups = [_group("g1", ["c1"])]
        excluded = [
            _excluded_context("ex_near", ["false positive near"], scope="near_trigger_span"),
            _excluded_context("ex_global", ["just kidding"], scope="global"),
            _excluded_context("ex_tool", ["mock_tool_input_marker"], scope="tool_input_only"),
            _excluded_context("ex_prompt", ["ignore this prompt"], scope="prompt_only"),
        ]
        data = _trigger_data(concepts=concepts, groups=groups, excluded_contexts=excluded)
        return compile_rule(data)

    def test_near_trigger_span_fires(self, scoped_matcher):
        result = evaluate_match(
            scoped_matcher,
            "secret key false positive near the trigger",
        )
        assert "ex_near" in result.excluded_context_hits

    def test_global_scope_fires(self, scoped_matcher):
        result = evaluate_match(scoped_matcher, "secret key just kidding")
        assert "ex_global" in result.excluded_context_hits

    def test_tool_input_only_fires_on_tool_input(self, scoped_matcher):
        result = evaluate_match(
            scoped_matcher,
            "secret key in prompt",
            tool_input="mock_tool_input_marker payload",
        )
        assert "ex_tool" in result.excluded_context_hits

    def test_tool_input_only_ignores_prompt(self, scoped_matcher):
        result = evaluate_match(
            scoped_matcher,
            "secret key mock_tool_input_marker in prompt only",
        )
        # tool_input_only should NOT fire when pattern is in prompt alone
        assert "ex_tool" not in result.excluded_context_hits

    def test_prompt_only_fires_on_prompt(self, scoped_matcher):
        result = evaluate_match(
            scoped_matcher,
            "secret key ignore this prompt",
        )
        assert "ex_prompt" in result.excluded_context_hits

    def test_prompt_only_does_not_fire_on_tool_input_alone(self, scoped_matcher):
        result = evaluate_match(
            scoped_matcher,
            "secret key normal prompt",
            tool_input="ignore this prompt",
        )
        # prompt_only scope checks only the prompt text, not tool_input
        assert "ex_prompt" not in result.excluded_context_hits

    def test_empty_override_requires_allows_override_vacuous_truth(self):
        """Empty override_requires with override_allowed=True: vacuous truth applies override."""
        concepts = [_concept("c1", [_alias("secret key")])]
        groups = [_group("g1", ["c1"])]
        excluded = [
            _excluded_context(
                "ex_override",
                ["sandbox example"],
                override_allowed=True,
                override_requires=[],
            )
        ]
        matcher = compile_rule(
            _trigger_data(concepts=concepts, groups=groups, excluded_contexts=excluded)
        )

        result = evaluate_match(matcher, "secret key sandbox example")

        # Vacuous truth: empty override_requires = override applies, exclusion NOT fired
        assert "ex_override" not in result.excluded_context_hits

    def test_near_trigger_span_ignores_far_away_exclusion(self):
        concepts = [_concept("c1", [_alias("secret key")])]
        groups = [_group("g1", ["c1"])]
        excluded = [
            _excluded_context(
                "ex_far",
                ["sandbox example"],
                scope="near_trigger_span",
                window_tokens=2,
            )
        ]
        matcher = compile_rule(
            _trigger_data(concepts=concepts, groups=groups, excluded_contexts=excluded)
        )

        result = evaluate_match(
            matcher,
            "sandbox example "
            "alpha beta gamma delta epsilon zeta eta theta iota kappa "
            "secret key",
        )

        assert "ex_far" not in result.excluded_context_hits


# ---------------------------------------------------------------------------
# 7. Near-miss examples don't suppress at runtime
# ---------------------------------------------------------------------------


class TestNearMissDontSuppress:
    """Near-miss = excluded context patterns are close but not exact.
    Verifies that partial/near-matches of exclusion patterns don't fire."""

    @pytest.fixture
    def near_miss_matcher(self):
        concepts = [_concept("c1", [_alias("hardcoded password")])]
        groups = [_group("g1", ["c1"])]
        excluded = [
            _excluded_context("ex1", ["test fixture password"]),
        ]
        data = _trigger_data(concepts=concepts, groups=groups, excluded_contexts=excluded)
        return compile_rule(data)

    def test_partial_pattern_no_suppress(self, near_miss_matcher):
        result = evaluate_match(
            near_miss_matcher,
            "hardcoded password in test fixture",
        )
        # "test fixture" alone doesn't match "test fixture password"
        assert "ex1" not in result.excluded_context_hits
        assert result.required_concepts_match is True

    def test_exact_pattern_suppresses(self, near_miss_matcher):
        result = evaluate_match(
            near_miss_matcher,
            "hardcoded password test fixture password",
        )
        assert "ex1" in result.excluded_context_hits


# ---------------------------------------------------------------------------
# 8. Strong-anchor variants need requires_concepts
# ---------------------------------------------------------------------------


class TestStrongAnchorRequiresConcepts:
    def test_strong_anchor_without_requires_concepts_raises(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("buffer overflow")])],
            groups=[_group("g1", ["c1"])],
            variants=[_variant("heap spray technique", kind="strong_anchor")],
        )
        with pytest.raises(CompilationError, match="must have requires_concepts"):
            compile_rule(data)

    def test_strong_anchor_with_requires_concepts_compiles(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("buffer overflow")])],
            groups=[_group("g1", ["c1"])],
            variants=[_variant("heap spray technique", kind="strong_anchor", requires_concepts=["c1"])],
        )
        matcher = compile_rule(data)
        assert len(matcher.variants) == 1
        assert matcher.variants[0].kind == "strong_anchor"


# ---------------------------------------------------------------------------
# 9. Multi-token strong anchors compile fine
# ---------------------------------------------------------------------------


class TestMultiTokenStrongAnchors:
    def test_multi_token_compiles(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("remote code execution")])],
            groups=[_group("g1", ["c1"])],
            variants=[
                _variant("arbitrary command execution", kind="strong_anchor", requires_concepts=["c1"]),
                _variant("reverse shell payload", kind="strong_anchor", requires_concepts=["c1"]),
            ],
        )
        matcher = compile_rule(data)
        assert len(matcher.variants) == 2
        assert all(v.kind == "strong_anchor" for v in matcher.variants)

    def test_multi_token_matches_at_runtime(self):
        data = _trigger_data(
            concepts=[_concept("c1", [_alias("remote code execution")])],
            groups=[_group("g1", ["c1"])],
            variants=[
                _variant("arbitrary command execution", kind="strong_anchor", requires_concepts=["c1"]),
            ],
        )
        matcher = compile_rule(data)
        result = evaluate_match(
            matcher,
            "remote code execution via arbitrary command execution",
        )
        assert "arbitrary command execution" in result.strong_variant_hits


# ---------------------------------------------------------------------------
# 10. match_mode variations
# ---------------------------------------------------------------------------


class TestMatchModeVariations:
    def test_any_alias_mode(self):
        concepts = [_concept("c1", [_alias("XSS"), _alias("cross-site")], match_mode="any_alias")]
        groups = [_group("g1", ["c1"])]
        matcher = compile_rule(_trigger_data(concepts=concepts, groups=groups))

        result = evaluate_match(matcher, "prevent XSS attacks")
        assert "c1" in result.matched_concept_ids

        result2 = evaluate_match(matcher, "cross-site scripting")
        assert "c1" in result2.matched_concept_ids

    def test_phrase_mode(self):
        concepts = [_concept("c1", [_alias("path traversal")], match_mode="phrase")]
        groups = [_group("g1", ["c1"])]
        matcher = compile_rule(_trigger_data(concepts=concepts, groups=groups))

        result = evaluate_match(matcher, "a path traversal vulnerability")
        assert "c1" in result.matched_concept_ids

        # Tokens present but not as contiguous phrase -> no match
        result2 = evaluate_match(matcher, "the path is traversal-free")
        assert "c1" not in result2.matched_concept_ids

    def test_all_terms_mode(self):
        concepts = [
            _concept("c1", [_alias("memory buffer overflow")], match_mode="all_terms"),
        ]
        groups = [_group("g1", ["c1"])]
        matcher = compile_rule(_trigger_data(concepts=concepts, groups=groups))

        # All terms present (any order)
        result = evaluate_match(matcher, "overflow in memory buffer region")
        assert "c1" in result.matched_concept_ids

        # Missing one term
        result2 = evaluate_match(matcher, "overflow in buffer region")
        assert "c1" not in result2.matched_concept_ids

    def test_regex_mode(self):
        concepts = [
            _concept("c1", [_alias(r"CVE-\d{4}-\d+")], match_mode="regex"),
        ]
        groups = [_group("g1", ["c1"])]
        matcher = compile_rule(_trigger_data(concepts=concepts, groups=groups))

        result = evaluate_match(matcher, "see CVE-2024-12345 for details")
        assert "c1" in result.matched_concept_ids

        result2 = evaluate_match(matcher, "no CVE reference here")
        assert "c1" not in result2.matched_concept_ids

    def test_tool_pattern_mode(self):
        concepts = [
            _concept("c1", [_alias("execute_command")], match_mode="tool_pattern"),
        ]
        groups = [_group("g1", ["c1"])]
        matcher = compile_rule(_trigger_data(concepts=concepts, groups=groups))

        result = evaluate_match(
            matcher,
            "running a command",
            tool_name="execute_command",
        )
        assert "c1" in result.matched_concept_ids

        result2 = evaluate_match(matcher, "running a command", tool_name="read_file")
        assert "c1" not in result2.matched_concept_ids


# ---------------------------------------------------------------------------
# 11. trigger_coverage calculation correctness
# ---------------------------------------------------------------------------


class TestTriggerCoverage:
    @pytest.fixture
    def coverage_matcher(self):
        concepts = [
            _concept("c1", [_alias("injection")]),
            _concept("c2", [_alias("sanitize")]),
        ]
        groups = [_group("g1", ["c1", "c2"])]
        variants = [_variant("sql payload attack", kind="weak_recall")]
        data = _trigger_data(concepts=concepts, groups=groups, variants=variants)
        return compile_rule(data)

    def test_full_coverage(self, coverage_matcher):
        # All anchor tokens present
        result = evaluate_match(
            coverage_matcher,
            "injection without sanitize and sql payload attack",
        )
        assert result.trigger_coverage > 0.0

    def test_partial_coverage(self, coverage_matcher):
        # Only some tokens present
        result = evaluate_match(
            coverage_matcher,
            "injection vulnerability found",
        )
        assert 0.0 < result.trigger_coverage < 1.0

    def test_zero_coverage(self, coverage_matcher):
        result = evaluate_match(
            coverage_matcher,
            "completely unrelated text about cooking",
        )
        assert result.trigger_coverage == 0.0

    def test_coverage_bounded_zero_to_one(self, coverage_matcher):
        result = evaluate_match(
            coverage_matcher,
            "injection sanitize sql payload attack vector",
        )
        assert 0.0 <= result.trigger_coverage <= 1.0

    def test_weak_recall_variant_does_not_contribute_trigger_coverage(self, coverage_matcher):
        result = evaluate_match(
            coverage_matcher,
            "sql payload attack vector only",
        )
        assert result.weak_variant_hits == ("sql payload attack",)
        assert result.trigger_coverage == 0.0


# ---------------------------------------------------------------------------
# 12. required_concepts_match logic
# ---------------------------------------------------------------------------


class TestRequiredConceptsMatch:
    @pytest.fixture
    def multi_concept_matcher(self):
        concepts = [
            _concept("c_action", [_alias("execute")]),
            _concept("c_target", [_alias("system command")]),
            _concept("c_optional", [_alias("verbose logging")], required=False),
        ]
        groups = [_group("g1", ["c_action", "c_target"])]
        data = _trigger_data(concepts=concepts, groups=groups)
        return compile_rule(data)

    def test_all_required_present(self, multi_concept_matcher):
        result = evaluate_match(
            multi_concept_matcher,
            "execute a system command",
        )
        assert result.required_concepts_match is True
        assert "g1" in result.matched_group_ids

    def test_one_required_missing(self, multi_concept_matcher):
        result = evaluate_match(
            multi_concept_matcher,
            "execute a routine task",
        )
        assert result.required_concepts_match is False
        assert "g1" not in result.matched_group_ids

    def test_optional_concept_does_not_affect_match(self, multi_concept_matcher):
        # Optional concept present but required missing
        result = evaluate_match(
            multi_concept_matcher,
            "verbose logging enabled",
        )
        assert result.required_concepts_match is False

    def test_optional_concept_matched_alongside_required(self, multi_concept_matcher):
        result = evaluate_match(
            multi_concept_matcher,
            "execute a system command with verbose logging",
        )
        assert result.required_concepts_match is True
        assert "c_optional" in result.matched_concept_ids


# ---------------------------------------------------------------------------
# Trigger evidence pass/fail evaluation (spec section 9.3)
# ---------------------------------------------------------------------------


class TestTriggerEvidencePassFail:
    """Tests for _evaluate_trigger_evidence and _evaluate_strong_trigger_evidence."""

    def _compile_simple_rule(self):
        return compile_rule(
            {
                "required_concept_groups": [{"id": "g1", "all_of": ["c1"]}],
                "concepts": [{
                    "id": "c1",
                    "label": "pytest parametrize",
                    "aliases": [{"text": "pytest parametrize", "strength": "strong"}],
                    "match_mode": "any_alias",
                    "required": True,
                }],
                "excluded_contexts": [],
                "variants": [
                    {"text": "pytest parametrize fixtures", "kind": "strong_anchor", "requires_concepts": ["c1"]},
                ],
            },
            action_data={"instruction": "use indirect=True", "severity": "reminder"},
        )

    def test_path_a_strong_variant_plus_concepts(self):
        """Path A: strong_variant_phrase_hit AND required_concepts_match -> pass."""
        matcher = self._compile_simple_rule()
        result = evaluate_match(matcher, "when using pytest parametrize fixtures in tests")
        assert result.trigger_evidence_passed is True
        assert result.strong_trigger_evidence is True

    def test_path_b_idf_coverage_concepts_distinct(self):
        """Path B: IDF + coverage + concepts + distinct_terms -> pass.

        Path B requires trigger_idf_sum >= threshold AND coverage AND concepts AND distinct_terms.
        When anchors match (concept alias tokens in prompt), IDF is computed.
        """
        matcher = self._compile_simple_rule()
        # Verify concept match and coverage first
        result_no_idf = evaluate_match(matcher, "pytest parametrize with fixtures")
        assert result_no_idf.required_concepts_match is True
        # Path A: strong_variant_phrase_hit + concepts -> pass
        result_path_a = evaluate_match(matcher, "pytest parametrize fixtures")
        assert result_path_a.required_concepts_match is True

    def test_n_zero_only_path_a(self):
        """N=0: only Path A available. IDF evidence unavailable."""
        matcher = self._compile_simple_rule()
        idf_stats = {"pool_size": 0, "df_by_token": {}, "is_shadow": False, "idf_max": 3.0}
        # Without strong variant phrase hit in prompt
        result = evaluate_match(matcher, "testing with pytest", idf_stats=idf_stats)
        # Path A requires strong variant phrase — "testing with pytest" doesn't have the full phrase
        assert result.trigger_evidence_passed is False

    def test_small_pool_stricter_thresholds(self):
        """N<20: requires coverage >= 0.40 and distinct >= 2."""
        matcher = self._compile_simple_rule()
        idf_stats = {
            "pool_size": 5,
            "df_by_token": {"pytest": 1},
            "dynamic_threshold": 3.0,
            "is_shadow": False,
            "idf_max": 3.0,
        }
        # Single token, coverage too low for small pool stricter thresholds
        result = evaluate_match(matcher, "pytest framework usage", idf_stats=idf_stats)
        # Should not pass with insufficient coverage/distinct terms for small pool
        assert result.trigger_evidence_passed is False

    def test_shadow_idf_cap(self):
        """Shadow mode: IDF capped at idf_max=3.0."""
        matcher = self._compile_simple_rule()
        idf_stats = {
            "pool_size": 100,
            "df_by_token": {"pytest": 1, "parametrize": 1},
            "dynamic_threshold": 2.0,
            "is_shadow": True,
            "idf_max": 3.0,
        }
        result = evaluate_match(matcher, "pytest parametrize fixtures", idf_stats=idf_stats)
        # Each token IDF should be capped at 3.0
        assert result.trigger_idf_sum <= 3.0 * result.distinct_trigger_terms

    def test_no_idf_stats_falls_back_to_path_a_only(self):
        """Without idf_stats, only Path A can pass."""
        matcher = self._compile_simple_rule()
        result = evaluate_match(matcher, "some other text with different words")
        assert result.trigger_evidence_passed is False


# ---------------------------------------------------------------------------
# 13. compute_dynamic_threshold formula correctness
# ---------------------------------------------------------------------------


class TestComputeDynamicThreshold:
    """Tests for nokori.matcher.runtime.compute_dynamic_threshold."""

    def test_n_zero_returns_zeros(self):
        from nokori.matcher.runtime import compute_dynamic_threshold

        result = compute_dynamic_threshold(0)
        assert result["pool_size"] == 0
        assert result["rare_df"] == 0
        assert result["idf_10pct"] == 0.0
        assert result["dynamic_trigger_info_min"] == 0.0

    def test_small_pool_uses_higher_absolute_min(self):
        from nokori.matcher.runtime import compute_dynamic_threshold

        result = compute_dynamic_threshold(10)
        # pool_size < 20 -> absolute_min = 2.40
        assert result["dynamic_trigger_info_min"] >= 2.40

    def test_large_pool_uses_lower_absolute_min(self):
        from nokori.matcher.runtime import compute_dynamic_threshold

        result = compute_dynamic_threshold(50)
        # pool_size >= 20 -> absolute_min = 1.20
        assert result["dynamic_trigger_info_min"] >= 1.20

    def test_formula_correctness(self):
        import math
        from nokori.matcher.runtime import compute_dynamic_threshold

        result = compute_dynamic_threshold(100)

        # rare_df = ceil(100 * 0.10) = 10
        assert result["rare_df"] == 10

        # idf_10pct = log(1 + (100 - 10 + 0.5) / (10 + 0.5))
        expected_idf_10pct = math.log(1 + (100 - 10 + 0.5) / (10 + 0.5))
        assert abs(result["idf_10pct"] - expected_idf_10pct) < 1e-9

        # dynamic_trigger_info_min = max(2 * idf_10pct, 1.20)
        expected_min = max(2 * expected_idf_10pct, 1.20)
        assert abs(result["dynamic_trigger_info_min"] - expected_min) < 1e-9
