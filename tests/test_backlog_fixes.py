"""Tests for #57 #59 #54 backlog fixes."""
import json
import subprocess
import sys

from nokori.config import Config
from nokori.db import open_db
from nokori.gate import marker as marker_io
from nokori.gate.marker import MarkerRule, prompt_hash
from nokori.lifecycle.evidence import MAX_EVIDENCE_LOG_ENTRIES, compute_evidence_append


def test_evidence_log_capped():
    score = 0
    log_json = "[]"
    for _ in range(MAX_EVIDENCE_LOG_ENTRIES + 10):
        score, log_json = compute_evidence_append(score, log_json, "shadow_hot", 1)
    entries = json.loads(log_json)
    assert len(entries) == MAX_EVIDENCE_LOG_ENTRIES
    assert entries[0]["kind"] == "shadow_hot"


def test_import_rolls_back_on_failure(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad_batch.json"
    payload = {
        "format": "nokori-export",
        "version": 2,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000001",
                "short_id": "good001",
                "trigger_text": "valid trigger one",
                "action": "ok",
            },
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "short_id": "bad002",
                "trigger_text": "also valid trigger",
                "action": "ok",
                "source_type": "not_a_real_type",
            },
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "NOKORI_DATA_DIR": str(data)}
    r = subprocess.run(
        [sys.executable, "-m", "nokori", "import", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode != 0
    db = open_db(data / "rules.db")
    try:
        assert db.fetchone("SELECT id FROM rules WHERE short_id='good001'") is None
    finally:
        db.close()


def test_dismiss_strips_gate_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    env = {"PATH": "/usr/bin:/bin", "NOKORI_DATA_DIR": str(tmp_path)}
    subprocess.run(
        [sys.executable, "-m", "nokori", "add",
         "--trigger", "deploy prisma", "--action", "use lease",
         "--source-type", "correction", "--confidence", "high"],
        check=True,
        env=env,
        capture_output=True,
    )
    from datetime import datetime, timezone

    db = open_db(cfg.db_path)
    try:
        rule = db.fetchone("SELECT id, short_id FROM rules LIMIT 1")
        ph = prompt_hash("deploy now")
        marker_io.write(
            cfg,
            "sess-x",
            "deploy now",
            [MarkerRule(rule["short_id"], "use lease", "correction")],
            ph=ph,
        )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?)",
                (rule["id"], "sess-x", ph, "hot", now),
            )
    finally:
        db.close()
    r = subprocess.run(
        [sys.executable, "-m", "nokori", "dismiss", rule["short_id"]],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert not cfg.marker_path("sess-x", ph).exists()


def test_no_gate_marker_when_injection_empty(monkeypatch, tmp_path):
    """#71: budget overflow → empty injection must not leave a gate marker."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.hooks import user_prompt_submit as ups
    from nokori.hooks.user_prompt_submit import handle
    from nokori.models import Rule, ScoredResult
    from nokori.search.retrieve import RetrievalResult

    cfg = Config.from_env()
    now = "2026-01-01T00:00:00Z"
    rule = Rule(
        id="r1",
        short_id="r1abcd",
        trigger_text="trigger",
        trigger_variants=[],
        search_terms={},
        behavior=None,
        action="do something important",
        rationale=None,
        source_type="correction",
        confidence="high",
        status="active",
        evidence_score=5,
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
    )
    hot = ScoredResult(rule=rule, retrieval_hot=True)

    def fake_retrieve(*_a, **_k):
        return RetrievalResult([hot], [], 1, "off"), [], []

    monkeypatch.setattr(ups, "retrieve_formal_and_shadow", fake_retrieve)
    monkeypatch.setattr(ups, "format_injection", lambda *_a, **_k: ("", []))

    out = handle({"session_id": "sess-empty", "prompt": "hello"}, cfg)
    assert out == {"continue": True}
    assert marker_io.read_latest_marker(cfg, "sess-empty") is None
