"""Regression tests for GitHub issues #82-#147 (selected)."""
import json

from nokori.config import Config
from nokori.db import open_db
from nokori.extract.extractor import _parse_candidates
from nokori.extract.jobs import transcript_hash
from nokori.gate.blocker import format_injection
from nokori.gate.marker import is_expired, strip_short_id_from_all_markers, Marker, MarkerRule
from nokori.lifecycle.maintenance import _days_since_iso
from nokori.lifecycle.promotion import record_shadow_hit
from nokori.lifecycle import transcript_index
from nokori.models import Rule, ScoredResult
from nokori.search.ranker import tier_results


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


def test_record_shadow_hit_returns_false_when_no_row(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(Config.from_env().db_path)
    try:
        assert record_shadow_hit(db, "missing-id", "proj-a") is False
    finally:
        db.close()


def test_transcript_index_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"user","message":{"content":"hi"}}\n', encoding="utf-8")
    transcript_index.record_session_transcript(cfg, p)
    p2 = tmp_path / "t2.jsonl"
    p2.write_text('{"type":"user","message":{"content":"hi"}}\n', encoding="utf-8")
    transcript_index.record_session_transcript(cfg, p2)
    assert transcript_index.lookup_previous(cfg, p2) == p.resolve()


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
                    trigger_text=f"trigger {i}",
                    trigger_variants=[],
                    search_terms={},
                    behavior=None,
                    action=f"action {i}" * 20,
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
                    created_at=now,
                    updated_at=now,
                ),
                rrf_score=0.5 - i * 0.01,
                retrieval_hot=True,
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
            trigger_text="t",
            trigger_variants=[],
            search_terms={},
            behavior=None,
            action="a",
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
            created_at=now,
            updated_at=now,
        ),
        rrf_score=0.008,
        matched_tokens=frozenset({"ab", "cd"}),
    )
    hot, warm = tier_results([weak])
    assert hot == []
    assert len(warm) == 1
