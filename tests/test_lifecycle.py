import json
from datetime import datetime, timedelta, timezone

from nokori.config import Config
from nokori.db import open_db
from nokori.lifecycle import hot_cache, maintenance, promotion


def _utcnow_iso(delta_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _make_rule(db, *, id_, status, last_hit_days_ago=None,
               source_origin="transcript_extraction",
               project_id=None, project_scope="project"):
    created = _utcnow_iso(-(last_hit_days_ago or 0))
    short = id_[:6]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "source_origin, status, severity, "
            "project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
            (id_, short, f"trigger {id_}", f"action {id_}",
             source_origin, status, "reminder",
             project_scope, project_id, created, created),
        )


def test_dormant_scan_is_noop(monkeypatch, tmp_path):
    """run_dormant_scan is now a no-op (dormant status removed)."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="aaa-old", status="active", last_hit_days_ago=45)
        moved = maintenance.run_dormant_scan(db)
        assert moved == 0
    finally:
        db.close()


def test_dormant_scan_always_returns_zero(monkeypatch, tmp_path):
    """Dormant scan is deprecated, always returns 0."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="zzz-old", status="active", last_hit_days_ago=45)
        moved = maintenance.run_dormant_scan(db)
        assert moved == 0
    finally:
        db.close()


def test_candidate_cleanup_deletes_fire_events(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="cand-fk", status="candidate",
                   last_hit_days_ago=30, source_origin="transcript_extraction")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, level, created_at) "
                "VALUES (?,?,?,?,?)",
                ("fe-1", "cand-fk", "s1", "hot", _utcnow_iso()),
            )
        deleted = maintenance.run_candidate_cleanup(db)
        assert deleted >= 1
        assert db.fetchone("SELECT 1 FROM rules WHERE id = 'cand-fk'") is None
        assert db.fetchone(
            "SELECT 1 FROM rule_fire_events WHERE rule_id = 'cand-fk'"
        ) is None
    finally:
        db.close()



def test_candidate_cleanup_removes_old(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="cand-1", status="candidate",
                   last_hit_days_ago=30, source_origin="transcript_extraction")
        _make_rule(db, id_="anti-1", status="candidate",
                   last_hit_days_ago=30, source_origin="external_source_material")
        deleted = maintenance.run_candidate_cleanup(db)
        # Default cand TTL=20 days; external_source_material TTL=40
        assert deleted == 1
        rows = {r["id"] for r in db.fetchall("SELECT id FROM rules")}
        assert "cand-1" not in rows
        assert "anti-1" in rows
    finally:
        db.close()


def test_dormant_reactivation_is_noop(monkeypatch, tmp_path):
    """reactivate_dormant_on_retrieval_hot is now a no-op."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="suppressed-1", status="suppressed", last_hit_days_ago=5)
        maintenance.reactivate_dormant_on_retrieval_hot(db, "suppressed-1")
        row = db.fetchone("SELECT status FROM rules WHERE id = 'suppressed-1'")
        # No-op - status unchanged
        assert row["status"] == "suppressed"
    finally:
        db.close()


def test_unmerge_restores_when_superseder_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, replacement_id, "
                "archived_reason, created_at, updated_at) "
                "VALUES ('old-1','old001',1,1,'v1','v1','t','a',"
                "'archived','reminder','global','gone-1','superseded',?,?)",
                (now, now),
            )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO maintenance_meta (key, last_run) VALUES ('unmerge_check', '2000-01-01T00:00:00Z') "
                "ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run"
            )
        restored = maintenance.run_unmerge_check(db)
        assert restored == 1
        row = db.fetchone("SELECT status, replacement_id FROM rules WHERE id='old-1'")
        assert row["status"] == "candidate"
        assert row["replacement_id"] is None
    finally:
        db.close()


def test_unique_promotion_project_ids_dedupes():
    from nokori.lifecycle.promotion import unique_promotion_project_ids

    raw = [
        {"key": "b:2026-01-01", "project_id": "proj-b", "date": "2026-01-01"},
        {"key": "b:2026-01-02", "project_id": "proj-b", "date": "2026-01-02"},
        {"key": "c:2026-01-01", "project_id": "proj-c", "date": "2026-01-01"},
    ]
    assert unique_promotion_project_ids(raw) == ["proj-b", "proj-c"]


def test_promotion_record_shadow_hit_is_noop(monkeypatch, tmp_path):
    """promotion.record_shadow_hit is now a no-op returning False."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_PROMOTION_ENABLED", "1")
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="rule-A", status="active", project_id="proj-A",
                   last_hit_days_ago=1)
        result = promotion.record_shadow_hit(db, "rule-A", "proj-B")
        assert result is False
    finally:
        db.close()


def test_promotion_skips_preference(monkeypatch, tmp_path):
    """promotion.record_shadow_hit is now a no-op."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_PROMOTION_ENABLED", "1")
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _make_rule(db, id_="pref-1", status="active", project_id="proj-A",
                   source_origin="transcript_extraction", last_hit_days_ago=1)
        result = promotion.record_shadow_hit(db, "pref-1", "proj-B")
        assert result is False
        row = db.fetchone("SELECT project_scope FROM rules WHERE id='pref-1'")
        assert row["project_scope"] == "project"
    finally:
        db.close()


def test_unmerge_check_restores_when_replacement_target_suppressed(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, created_at, updated_at) "
                "VALUES ('new-1', 'new111', 1, 1, 'v1', 'v1', 'new trigger', 'new action', "
                "'suppressed', 'reminder', 'project', ?, ?)",
                (now, now),
            )
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, replacement_id, archived_reason, "
                "created_at, updated_at) "
                "VALUES ('old-1', 'old111', 1, 1, 'v1', 'v1', 'old trigger', 'old action', "
                "'archived', 'reminder', 'project', 'new-1', 'superseded', ?, ?)",
                (now, now),
            )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO maintenance_meta (key, last_run) VALUES ('unmerge_check', '2000-01-01T00:00:00Z') "
                "ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run"
            )
        restored = maintenance.run_unmerge_check(db)
        assert restored == 1
        row = db.fetchone(
            "SELECT status, replacement_id FROM rules WHERE id = 'old-1'"
        )
        assert row["status"] == "candidate"
        assert row["replacement_id"] is None
    finally:
        db.close()



def test_hot_cache_returns_none_when_no_path(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        assert hot_cache.maybe_inject({}, cfg, db) is None
    finally:
        db.close()


def test_find_previous_transcript_picks_newest_older_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
    older = tmp_path / "older.jsonl"
    older.write_text('{"type":"user","message":"old"}\n')
    current = tmp_path / "current.jsonl"
    current.write_text('{"type":"user","message":"new"}\n')
    import os
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(current, (1_700_000_100, 1_700_000_100))
    assert hot_cache.find_previous_transcript(current) == older.resolve()


def test_hot_cache_injects_from_previous_session(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
    cfg = Config.from_env()
    previous = tmp_path / "previous.jsonl"
    previous.write_text("\n".join([
        json.dumps({"type": "user", "message": "first prompt"}),
        json.dumps({"type": "assistant", "message": "ok"}),
        json.dumps({"type": "user", "message": "do not force push"}),
    ]) + "\n")
    current = tmp_path / "current.jsonl"
    current.write_text('{"type":"user","message":"new session"}\n')
    import os
    os.utime(previous, (1_700_000_000, 1_700_000_000))
    os.utime(current, (1_700_000_100, 1_700_000_100))
    db = open_db(cfg.db_path)
    try:
        text = hot_cache.maybe_inject({"transcript_path": str(current)}, cfg, db)
        assert text and "do not force push" in text
        assert "new session" not in text
    finally:
        db.close()


def test_hot_cache_skips_when_previous_extracted(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    previous = tmp_path / "previous.jsonl"
    previous.write_text(
        json.dumps({"type": "user", "message": "do not force push"}) + "\n"
    )
    current = tmp_path / "current.jsonl"
    current.write_text('{"type":"user","message":"new session"}\n')
    import os
    os.utime(previous, (1_700_000_000, 1_700_000_000))
    os.utime(current, (1_700_000_100, 1_700_000_100))
    db = open_db(cfg.db_path)
    try:
        hot_cache.mark_extracted(db, previous, previous.stat().st_mtime)
        assert hot_cache.maybe_inject({"transcript_path": str(current)}, cfg, db) is None
    finally:
        db.close()
