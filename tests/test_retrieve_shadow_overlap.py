"""Shadow pool must not double-count rules already in the formal pool."""
from unittest.mock import patch

from nokori.config import Config
from nokori.db import open_db
from nokori.models import Rule, ScoredResult
from nokori.search.retrieve import RetrievalResult, retrieve_formal_and_shadow


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
        trigger_variants=[],
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
        )
        hot_shadow_only = ScoredResult(
            rule=shadow_rules[1],
            rrf_score=0.01,
            bm25_score=1.0,
            matched_trigger_tokens=frozenset({"git"}),
        )
        def fake_retrieve(_prompt, rules, _db, _cfg, **_kwargs):
            rule_ids = {r.id for r in rules}
            if rule_ids == {"shared-rule-id"}:
                return RetrievalResult(
                    hot=[hot_shared],
                    warm=[],
                    bm25_matches=1,
                    embed_mode="off",
                )
            if rule_ids == {"shadow-only"}:
                return RetrievalResult(
                    hot=[hot_shadow_only],
                    warm=[],
                    bm25_matches=1,
                    embed_mode="off",
                )
            return RetrievalResult(hot=[], warm=[], bm25_matches=0, embed_mode="off")

        with patch(
            "nokori.search.retrieve.retrieve_and_tier",
            side_effect=fake_retrieve,
        ):
            formal_result, shadow_hot, _shadow_warm = retrieve_formal_and_shadow(
                "git push --force",
                formal_rules,
                shadow_rules,
                db,
                cfg,
            )

        assert [r.rule.id for r in formal_result.hot] == ["shared-rule-id"]
        assert [r.rule.id for r in shadow_hot] == ["shadow-only"]
        assert "shared-rule-id" not in {r.rule.id for r in shadow_hot}
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
        )
        shadow_hit = ScoredResult(
            rule=shadow,
            rrf_score=0.03,
            bm25_score=2.0,
            matched_trigger_tokens=frozenset({"git", "force", "push"}),
        )

        def fake_retrieve(_prompt, rules, _db, _cfg, **_kwargs):
            rule_ids = {r.id for r in rules}
            if rule_ids == {"formal-rule", "shadow-rule"}:
                return RetrievalResult(hot=[shadow_hit], warm=[], bm25_matches=2, embed_mode="off")
            if rule_ids == {"formal-rule"}:
                return RetrievalResult(hot=[formal_hit], warm=[], bm25_matches=1, embed_mode="off")
            if rule_ids == {"shadow-rule"}:
                return RetrievalResult(hot=[shadow_hit], warm=[], bm25_matches=1, embed_mode="off")
            return RetrievalResult(hot=[], warm=[], bm25_matches=0, embed_mode="off")

        with patch("nokori.search.retrieve.retrieve_and_tier", side_effect=fake_retrieve):
            formal_result, shadow_hot, _shadow_warm = retrieve_formal_and_shadow(
                "git push --force",
                [formal],
                [shadow],
                db,
                cfg,
            )

        assert [r.rule.id for r in formal_result.hot] == ["formal-rule"]
        assert [r.rule.id for r in shadow_hot] == ["shadow-rule"]
    finally:
        db.close()
