"""Regression tests for GitHub issues #82-#147 (selected)."""
import json

from nokori.config import Config
from nokori.extract.extractor import _parse_candidates
from nokori.extract.jobs import transcript_hash
from nokori.gate.blocker import format_injection
from nokori.gate.marker import Marker, MarkerRule, is_expired, strip_short_id_from_all_markers
from nokori.lifecycle.maintenance import _days_since_iso
from nokori.models import Rule, ScoredResult
from nokori.search.selector import select_injection


def test_days_since_iso_clamps_negative():
    future = "2099-01-01T00:00:00Z"
    assert _days_since_iso(future) == 0


def test_transcript_hash_subsecond():
    h1 = transcript_hash(__import__("pathlib").Path("/tmp/t.jsonl"), 1.0)
    h2 = transcript_hash(__import__("pathlib").Path("/tmp/t.jsonl"), 1.5)
    assert h1 != h2


def test_parse_candidates_false_when_all_invalid():
    raw = json.dumps([{"trigger": "", "action": "x"}])
    cands, ok = _parse_candidates(raw)
    assert cands == []
    assert ok is False


def test_gate_ttl_zero_never_expires():
    m = Marker("s", "ph", "2026-01-01T00:00:00Z", [])
    assert is_expired(m, 0) is False


def test_strip_short_id_from_all_markers(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    from nokori.gate import marker as marker_io

    ph = "abc123"
    marker_io.write(
        cfg,
        "sess",
        "prompt",
        [MarkerRule("deadbeef", "act", "correction", None)],
        ph=ph,
    )
    strip_short_id_from_all_markers(cfg, "deadbeef")
    assert marker_io.read(cfg, "sess", prompt_hash_value=ph) is None


def test_format_injection_truncation_logs_subset():
    now = "2026-01-01T00:00:00Z"
    rules = []
    for i in range(5):
        rules.append(
            ScoredResult(
                rule=Rule(
                    id=f"id{i}",
                    short_id=f"sid{i:02d}",
                    schema_version=1,
                    rule_version=1,
                    created_by_pipeline_version="test",
                    runtime_policy_version="test",
                    last_rewritten_by_role=None,
                    status="active",
                    severity="reminder",
                    trigger_canonical=f"trigger {i}",
                    action_instruction=f"action {i}" * 20,
                    created_at=now,
                    updated_at=now,
                ),
                rrf_score=0.5 - i * 0.01,
            )
        )
    text, logged = format_injection(rules[:1], rules[1:], max_chars=400)
    assert text
    assert len(logged) < len(rules)


def test_tier_singleton_requires_strong_match():
    now = "2026-01-01T00:00:00Z"
    weak = ScoredResult(
        rule=Rule(
            id="r1",
            short_id="abcd12",
            schema_version=1,
            rule_version=1,
            created_by_pipeline_version="test",
            runtime_policy_version="test",
            last_rewritten_by_role=None,
            status="active",
            severity="reminder",
            trigger_canonical="t",
            action_instruction="a",
            created_at=now,
            updated_at=now,
        ),
        rrf_score=0.008,
        matched_trigger_tokens=frozenset({"ab", "cd"}),
    )
    sel = select_injection([weak], max_injection_chars=1500)
    assert sel.hot == []
    assert len(sel.warm) == 1
