from datetime import UTC, datetime

from nokori.models import Rule, ScoredResult
from nokori.search import bm25, scorer as ranker
from nokori.search.applicability import meets_min_evidence
from nokori.search.selector import select_injection


def _rule(short, trigger, *, variants=(), terms_zh=(), action="do x", status="active",
          trigger_canonical_zh=None, action_instruction_zh=None):
    now = datetime.now(UTC).isoformat()
    return Rule(
        id=f"id-{short}",
        short_id=short,
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="test",
        runtime_policy_version="test",
        last_rewritten_by_role=None,
        status=status,
        severity="reminder",
        trigger_canonical=trigger,
        trigger_canonical_zh=trigger_canonical_zh,
        trigger_variants=list(variants),
        search_terms={"zh": list(terms_zh)} if terms_zh else {},
        action_instruction=action,
        action_instruction_zh=action_instruction_zh,
        created_at=now,
        updated_at=now,
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
    assert "force" in results[0].matched_trigger_tokens
    assert "push" in results[0].matched_trigger_tokens


def test_tier_hot_when_top1_dominant():
    rules = [
        _rule("aaa111", "force push to main", variants=("git push --force",),
              action="use lease"),
        _rule("bbb222", "use yarn install", action="don't"),
    ]
    bm = bm25.search("git push --force my branch", rules)
    fused = ranker.rrf_fuse(bm, [])
    sel = select_injection(fused, max_injection_chars=1500)
    assert sel.hot
    assert sel.hot[0].rule.short_id == "aaa111"


def test_select_injection_is_status_agnostic():
    # select_injection itself does not filter by status; status filtering happens
    # upstream in applicability (_fetch_formal_and_shadow). This test verifies
    # that if a suppressed rule reaches ranking, it still appears in results.
    rules = [
        _rule("aaa111", "force push", variants=("git push --force",),
              action="use lease", status="suppressed"),
        _rule("bbb222", "yarn install", action="don't"),
    ]
    bm = bm25.search("git push --force", rules)
    fused = ranker.rrf_fuse(bm, [])
    sel = select_injection(fused, max_injection_chars=1500)
    all_results = sel.hot + sel.warm
    assert any(r.rule.short_id == "aaa111" for r in all_results)


def test_tier_min_evidence_blocks_single_token_match():
    rules = [
        _rule("aaa111", "totally unrelated thing topic foo", action="x"),
        _rule("bbb222", "another rule about thing", action="y"),
    ]
    bm = bm25.search("thing", rules)
    fused = ranker.rrf_fuse(bm, [])
    eligible = [r for r in fused if meets_min_evidence(r)]
    sel = select_injection(eligible, max_injection_chars=1500)
    assert sel.hot == []
    assert sel.warm == []


def test_meets_min_evidence_rejects_embedding_only_match():
    # A result with high cosine but no BM25 trigger/variant tokens fails min_evidence.
    # Embedding-only matches are not sufficient for injection.
    rules = [_rule("aaa111", "force push to main", action="use lease")]
    result = bm25.ScoredResult(rule=rules[0], cosine=0.99)
    assert meets_min_evidence(result) is False


def test_rrf_fuse_preserves_embedding_profile_metadata():
    rule = _rule("aaa111", "force push to main", action="use lease")
    bm = ScoredResult(rule=rule, matched_trigger_tokens=frozenset({"force"}))
    emb = ScoredResult(
        rule=rule,
        cosine=0.92,
        embedding_profile_bucket="code_or_cli",
        embedding_profile_version="profile-v1",
        embedding_profile_unknown=False,
    )

    fused = ranker.rrf_fuse([bm], [emb])

    assert fused[0].embedding_profile_bucket == "code_or_cli"
    assert fused[0].embedding_profile_version == "profile-v1"
    assert fused[0].embedding_profile_unknown is False


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

    rules = [
        _rule(
            f"r{i:04x}",
            f"trigger number {i} unique words alpha{i} beta{i}",
            action=f"action {i}",
        )
        for i in range(500)
    ]
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
    """trigger_canonical_zh content is indexed and searchable via BM25."""
    bm25._INDEX_CACHE.clear()
    rules = [
        _rule("zh001", "Force push to shared branch",
              trigger_canonical_zh="强制推送到共享分支",
              action="use --force-with-lease", action_instruction_zh="使用 --force-with-lease"),
        _rule("zh002", "Update database schema",
              trigger_canonical_zh="更新数据库模式",
              action="run migration first", action_instruction_zh="先运行迁移"),
    ]
    results = bm25.search("强制推送", rules)
    assert len(results) > 0
    assert results[0].rule.short_id == "zh001"
