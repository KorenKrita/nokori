"""Gate injection text budget."""
from nokori.gate.blocker import format_injection
from nokori.models import Rule
from nokori.search.ranker import ScoredResult


def _scored(rule: Rule, score: float = 1.0) -> ScoredResult:
    return ScoredResult(rule=rule, rrf_score=score, retrieval_hot=True)


def test_format_injection_includes_footer_within_max_chars():
    rule = Rule(
        id="r1",
        short_id="abcd12",
        trigger_text="deploy prisma",
        trigger_variants=[],
        search_terms={},
        behavior=None,
        action="use migrate deploy",
        rationale=None,
        source_type="correction",
        confidence="high",
        status="active",
        evidence_score=0,
        evidence_log=[],
        hit_count=0,
        last_hit=None,
        shadow_hit_count=0,
        promotion_evidence=[],
        project_scope="global",
        project_id=None,
        superseded_by=None,
        archived_reason=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    hot = [_scored(rule)]
    text, logged = format_injection(hot, [], max_chars=200, dismiss_phrase="dismiss")
    assert len(text) <= 500
    assert "dismiss" in text
    assert logged == [(rule.id, "hot")]

    tiny, tiny_logged = format_injection(hot, [], max_chars=50, dismiss_phrase="dismiss")
    assert tiny == ""
    assert tiny_logged == []
