"""Shadow pool must not double-count rules already in the formal pool."""
from unittest.mock import patch

from nokori.config import Config
from nokori.db import open_db
from nokori.models import Rule, ScoredResult
from nokori.search.engine import RetrievalEngine


def _rule(rule_id: str, *, project_id: str = "proj-a") -> Rule:
    return Rule(
        id=rule_id,
        short_id=rule_id[:6],
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="v1",
        runtime_policy_version="v1",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical="git force push",
        trigger_variants="[]",
        search_terms={"en": [], "zh": []},
        action_instruction="use lease",
        source_origin="transcript_extraction",
        project_scope="project",
        project_id=project_id,
        created_at="2026-05-30T00:00:00Z",
        updated_at="2026-05-30T00:00:00Z",
    )


def test_shadow_hot_excludes_formal_overlap(monkeypatch, tmp_path):
    """Overlapping rule id in formal+shadow pools counts only toward formal HOT."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        shared = _rule("shared-rule-id", project_id="my-proj")
        formal_rules = [shared]
        shadow_rules = [shared, _rule("shadow-only", project_id="other-proj")]

        hot_shared = ScoredResult(
            rule=shared,
            rrf_score=0.02,
            bm25_score=1.5,
            matched_trigger_tokens=frozenset({"git", "force"}),
            trigger_idf_sum=2.0,
            ranking_utility=3.0,
            trigger_evidence_passed=True,
            level="hot",
        )
        hot_shadow_only = ScoredResult(
            rule=shadow_rules[1],
            rrf_score=0.01,
            bm25_score=1.0,
            matched_trigger_tokens=frozenset({"git"}),
            trigger_idf_sum=1.5,
            ranking_utility=2.0,
            trigger_evidence_passed=True,
            level="hot",
        )

        def fake_score(prompt, rules, **kwargs):
            by_id = {r.rule.id: r for r in (hot_shared, hot_shadow_only)}
            return [by_id[r.id] for r in rules if r.id in by_id]

        engine = RetrievalEngine(cfg, db)
        with (
            patch.object(engine._scorer, "score", side_effect=fake_score),
            patch(
                "nokori.search.engine.evaluate_evidence",
                side_effect=lambda result, prompt, **kwargs: result,
            ),
        ):
            result = engine.retrieve(
                "git push --force",
                formal_rules,
                shadow_rules,
            )

        assert [r.rule.id for r in result.hot] == ["shared-rule-id"]
        assert [r.rule.id for r in result.shadow_hot] == ["shadow-only"]
        assert "shared-rule-id" not in {r.rule.id for r in result.shadow_hot}
    finally:
        db.close()


def test_shadow_selection_does_not_consume_formal_hot_slot(monkeypatch, tmp_path):
    """Shadow-only candidates cannot starve formal rules from their injection slots."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        formal = _rule("formal-rule", project_id="my-proj")
        shadow = _rule("shadow-rule", project_id="my-proj")
        formal_hit = ScoredResult(
            rule=formal,
            rrf_score=0.02,
            bm25_score=1.5,
            matched_trigger_tokens=frozenset({"git", "force"}),
            trigger_idf_sum=2.0,
            ranking_utility=3.0,
            trigger_evidence_passed=True,
            level="hot",
        )
        shadow_hit = ScoredResult(
            rule=shadow,
            rrf_score=0.03,
            bm25_score=2.0,
            matched_trigger_tokens=frozenset({"git", "force", "push"}),
            trigger_idf_sum=2.5,
            ranking_utility=4.0,
            trigger_evidence_passed=True,
            level="hot",
        )

        def fake_score(prompt, rules, **kwargs):
            by_id = {r.rule.id: r for r in (formal_hit, shadow_hit)}
            return [by_id[r.id] for r in rules if r.id in by_id]

        engine = RetrievalEngine(cfg, db)
        with (
            patch.object(engine._scorer, "score", side_effect=fake_score),
            patch(
                "nokori.search.engine.evaluate_evidence",
                side_effect=lambda result, prompt, **kwargs: result,
            ),
        ):
            result = engine.retrieve(
                "git push --force",
                [formal],
                [shadow],
            )

        assert [r.rule.id for r in result.hot] == ["formal-rule"]
        assert [r.rule.id for r in result.shadow_hot] == ["shadow-rule"]
    finally:
        db.close()


def test_retrieve_scores_union_once(monkeypatch, tmp_path):
    """Formal+shadow retrieve must score the union in a single scorer call."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        formal = _rule("formal-rule")
        shadow = _rule("shadow-rule")
        calls: list[set[str]] = []

        def fake_score(prompt, rules, **kwargs):
            calls.append({r.id for r in rules})
            return []

        engine = RetrievalEngine(cfg, db)
        with patch.object(engine._scorer, "score", side_effect=fake_score):
            engine.retrieve("git push --force", [formal], [shadow])

        assert len(calls) == 1
        assert calls[0] == {"formal-rule", "shadow-rule"}
    finally:
        db.close()
