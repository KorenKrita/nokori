import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nokori.config import Config
from nokori.db import open_db
from nokori.lifecycle import evidence, hot_cache, maintenance, promotion


def _utcnow_iso(delta_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _make_rule(db, *, id_, status, last_hit_days_ago=None, source_type="correction",
               project_id=None, project_scope="project"):
    last_hit = _utcnow_iso(-last_hit_days_ago) if last_hit_days_ago is not None else None
    created = _utcnow_iso(-(last_hit_days_ago or 0))
    short = id_[:6]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
            "confidence, status, project_scope, project_id, created_at, updated_at, "
            "last_hit) VALUES (?,?,?,?,?,'high',?,?,?,?,?,?)",
            (id_, short, f"trigger {id_}", f"action {id_}",
             source_type, status, project_scope, project_id, created, created, last_hit),
        )


def test_dormant_scan_moves_old_active(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="aaa-old", status="active", last_hit_days_ago=45)
        _make_rule(db, id_="bbb-fresh", status="active", last_hit_days_ago=5)
        moved = maintenance.run_dormant_scan(db)
        assert moved == 1
        old = db.fetchone("SELECT status FROM rules WHERE id = 'aaa-old'")
        fresh = db.fetchone("SELECT status FROM rules WHERE id = 'bbb-fresh'")
        assert old["status"] == "dormant"
        assert fresh["status"] == "active"
    finally:
        db.close()


def test_dormant_scan_respects_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="zzz-old", status="active", last_hit_days_ago=45)
        maintenance.run_dormant_scan(db)
        # Add another stale rule. Without interval gating, this would also move.
        _make_rule(db, id_="yyy-old", status="active", last_hit_days_ago=45)
        moved_again = maintenance.run_dormant_scan(db)
        assert moved_again == 0  # interval not yet elapsed
    finally:
        db.close()


def test_candidate_cleanup_removes_old(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="cand-1", status="candidate",
                   last_hit_days_ago=30, source_type="correction")
        _make_rule(db, id_="anti-1", status="candidate",
                   last_hit_days_ago=30, source_type="anti_pattern")
        deleted = maintenance.run_candidate_cleanup(db)
        # Default cand TTL=20 days; anti_pattern TTL=40
        assert deleted == 1
        rows = {r["id"] for r in db.fetchall("SELECT id FROM rules")}
        assert "cand-1" not in rows
        assert "anti-1" in rows
    finally:
        db.close()


def test_dormant_reactivation(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="dormant-1", status="dormant", last_hit_days_ago=5)
        maintenance.reactivate_dormant_on_retrieval_hot(db, "dormant-1")
        row = db.fetchone("SELECT status FROM rules WHERE id = 'dormant-1'")
        assert row["status"] == "active"
    finally:
        db.close()


def test_promotion_after_three_projects(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="rule-A", status="active", project_id="proj-A",
                   last_hit_days_ago=1)
        # Three different projects record shadow hits.
        promo1 = promotion.record_shadow_hit(db, "rule-A", "proj-B")
        promo2 = promotion.record_shadow_hit(db, "rule-A", "proj-C")
        promo3 = promotion.record_shadow_hit(db, "rule-A", "proj-D")
        assert promo1 is False
        assert promo2 is False
        assert promo3 is True
        row = db.fetchone("SELECT project_scope FROM rules WHERE id = 'rule-A'")
        assert row["project_scope"] == "global"
    finally:
        db.close()


def test_promotion_skips_preference(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="pref-1", status="active", project_id="proj-A",
                   source_type="preference", last_hit_days_ago=1)
        promotion.record_shadow_hit(db, "pref-1", "proj-B")
        promotion.record_shadow_hit(db, "pref-1", "proj-C")
        promotion.record_shadow_hit(db, "pref-1", "proj-D")
        row = db.fetchone("SELECT project_scope, cross_project_hits FROM rules WHERE id='pref-1'")
        assert row["project_scope"] == "project"
        assert row["cross_project_hits"] == 0
    finally:
        db.close()


def test_evidence_active_days():
    log = [
        {"kind": "x", "points": 1, "at": "2026-01-01T10:00:00Z"},
        {"kind": "x", "points": 1, "at": "2026-01-01T20:00:00Z"},
        {"kind": "x", "points": 1, "at": "2026-01-02T05:00:00Z"},
    ]
    assert evidence.evidence_active_days(log) == 2


def test_hot_cache_returns_none_when_no_path(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        assert hot_cache.maybe_inject({}, cfg, db) is None
    finally:
        db.close()


def test_unmerge_check_restores_when_superseded_target_dormant(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
                "confidence, status, project_scope, created_at, updated_at) "
                "VALUES ('new-1', 'new111', 'new trigger', 'new action', 'correction', "
                "'high', 'dormant', 'project', ?, ?)",
                (now, now),
            )
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
                "confidence, status, project_scope, superseded_by, created_at, updated_at) "
                "VALUES ('old-1', 'old111', 'old trigger', 'old action', 'correction', "
                "'high', 'merged', 'project', 'new-1', ?, ?)",
                (now, now),
            )
        restored = maintenance.run_unmerge_check(db)
        assert restored == 1
        row = db.fetchone(
            "SELECT status, superseded_by FROM rules WHERE id = 'old-1'"
        )
        assert row["status"] == "dormant"
        assert row["superseded_by"] is None
    finally:
        db.close()


def test_hot_cache_injects_user_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "user", "message": "first prompt"}),
        json.dumps({"type": "assistant", "message": "ok"}),
        json.dumps({"type": "user", "message": "do not force push"}),
    ]) + "\n")
    db = open_db(cfg.db_path)
    try:
        text = hot_cache.maybe_inject({"transcript_path": str(transcript)}, cfg, db)
        assert text and "do not force push" in text
    finally:
        db.close()
