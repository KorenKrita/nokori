"""Gate injection text budget."""
from nokori.gate.blocker import format_injection
from nokori.models import Rule, ScoredResult


def _scored(rule: Rule, score: float = 1.0) -> ScoredResult:
    return ScoredResult(rule=rule, rrf_score=score)


def test_format_injection_includes_footer_within_max_chars():
    rule = Rule(
        id="r1",
        short_id="abcd12",
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="test",
        runtime_policy_version="test",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical="deploy prisma",
        action_instruction="use migrate deploy",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    hot = [_scored(rule)]
    text, logged = format_injection(hot, [], max_chars=200, dismiss_phrase="dismiss")
    assert len(text) <= 500
    assert "dismiss" in text
    assert logged == [(rule.id, "hot")]

    tiny, tiny_logged = format_injection(hot, [], max_chars=50, dismiss_phrase="dismiss")
    # Injection budget floor (500) still renders a small HOT block when max_chars is tiny.
    assert tiny != ""
    assert "dismiss" in tiny
    assert tiny_logged == [(rule.id, "hot")]
