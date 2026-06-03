from datetime import datetime, timezone

from nokori.models import Rule
from nokori.search import bm25, ranker


def _rule(short, trigger, *, variants=(), terms_zh=(), action="do x", status="active",
          conf="high", source_type="correction",
          trigger_text_zh=None, action_zh=None,
          behavior_zh=None, rationale_zh=None):
    now = datetime.now(timezone.utc).isoformat()
    return Rule(
        id=f"id-{short}",
        short_id=short,
        trigger_text=trigger,
        trigger_variants=list(variants),
        search_terms={"zh": list(terms_zh)} if terms_zh else {},
        behavior=None,
        action=action,
        rationale=None,
        source_type=source_type,
        confidence=conf,
        status=status,
        evidence_score=0,
        evidence_log=[],
        hit_count=0,
        last_hit=None,
        shadow_hit_count=0,
        promotion_evidence=[],
        project_scope="project",
        project_id=None,
        superseded_by=None,
        archived_reason=None,
        created_at=now,
        updated_at=now,
        trigger_text_zh=trigger_text_zh,
        action_zh=action_zh,
        behavior_zh=behavior_zh,
        rationale_zh=rationale_zh,
    )


def test_bm25_ranks_relevant_first():
    rules = [
        _rule("aaa111", "Never force push to a shared branch",
              variants=("git push --force",), action="use --force-with-lease"),
        _rule("bbb222", "Update prisma ORM major version",
              action="run migration in shadow first"),
        _rule("ccc333", "Use pnpm instead of npm in this repo",
              action="run pnpm install"),
    ]
    results = bm25.search("git push --force my work", rules)
    assert len(results) >= 1
    assert results[0].rule.short_id == "aaa111"


def test_bm25_returns_empty_for_no_overlap():
    rules = [_rule("aaa111", "Never force push", action="use lease")]
    results = bm25.search("totally unrelated content", rules)
    assert results == []


def test_bm25_matched_tokens_populated():
    rules = [_rule("aaa111", "force push to main", action="use lease")]
    results = bm25.search("force push", rules)
    assert "force" in results[0].matched_tokens
    assert "push" in results[0].matched_tokens


def test_tier_hot_when_top1_dominant():
    rules = [
        _rule("aaa111", "force push to main", variants=("git push --force",),
              action="use lease"),
        _rule("bbb222", "use yarn install", action="don't"),
    ]
    bm = bm25.search("git push --force my branch", rules)
    fused = ranker.rrf_fuse(bm, [])
    hot, warm = ranker.tier_results(fused)
    assert hot
    assert hot[0].rule.short_id == "aaa111"


def test_tier_dormant_promotes_warm_with_retrieval_hot():
    rules = [
        _rule("aaa111", "force push", variants=("git push --force",),
              action="use lease", status="dormant"),
        _rule("bbb222", "yarn install", action="don't"),
    ]
    bm = bm25.search("git push --force", rules)
    fused = ranker.rrf_fuse(bm, [])
    hot, warm = ranker.tier_results(fused)
    assert hot == []
    if warm:
        assert any(w.retrieval_hot for w in warm if w.rule.short_id == "aaa111")


def test_tier_min_evidence_blocks_single_token_match():
    rules = [
        _rule("aaa111", "totally unrelated thing topic foo", action="x"),
        _rule("bbb222", "another rule about thing", action="y"),
    ]
    bm = bm25.search("thing", rules)
    fused = ranker.rrf_fuse(bm, [])
    hot, warm = ranker.tier_results(fused)
    assert hot == []
    assert warm == []


def test_chinese_query():
    rules = [
        _rule("aaa111", "ORM major version upgrade",
              terms_zh=("数据库迁移", "升级版本"),
              action="先在影子库跑迁移"),
    ]
    bm = bm25.search("升级版本到新数据库", rules)
    assert any(r.rule.short_id == "aaa111" for r in bm)


def test_perf_500_rules_under_50ms():
    import time

    rules = []
    for i in range(500):
        rules.append(
            _rule(
                f"r{i:04x}",
                f"trigger number {i} unique words alpha{i} beta{i}",
                action=f"action {i}",
            )
        )
    rules.append(
        _rule("hit001", "force push to main branch",
              variants=("git push --force",), action="use lease")
    )
    start = time.perf_counter()
    results = bm25.search("git push --force my branch", rules)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert results[0].rule.short_id == "hit001"
    assert elapsed_ms < 100, f"BM25 took {elapsed_ms:.1f}ms (budget 50ms × 2)"


def test_bm25_matches_chinese_trigger_zh():
    """trigger_text_zh content is indexed and searchable via BM25."""
    bm25.clear_index_cache()
    rules = [
        _rule("zh001", "Force push to shared branch",
              trigger_text_zh="强制推送到共享分支",
              action="use --force-with-lease", action_zh="使用 --force-with-lease"),
        _rule("zh002", "Update database schema",
              trigger_text_zh="更新数据库模式",
              action="run migration first", action_zh="先运行迁移"),
    ]
    results = bm25.search("强制推送", rules)
    assert len(results) > 0
    assert results[0].rule.short_id == "zh001"
