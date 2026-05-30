from nokori.config import Config
from nokori.db import open_db
from nokori.hooks.user_prompt_submit import _run_shadow_pool, handle
from nokori.utils.project import resolve_project_id
from nokori.utils.time import now_iso


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def test_shadow_pool_noop_when_promotion_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_PROMOTION_ENABLED", "0")
    cfg = Config.from_env()
    assert cfg.promotion_enabled is False
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, "
                "source_type, confidence, status, project_scope, project_id, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "rule-x", "rulex1", "git push force remote",
                    "use lease", "correction", "high", "active", "project",
                    "other-proj", now, now,
                ),
            )
        _run_shadow_pool(db, "git push force remote", "my-proj", cfg)
        row = db.fetchone(
            "SELECT cross_project_hits FROM rules WHERE id = 'rule-x'"
        )
        assert row["cross_project_hits"] == 0
    finally:
        db.close()


def test_handle_runs_shadow_when_formal_pool_empty(monkeypatch, tmp_path):
    """Scene C: new project with zero local rules still records shadow HOT hits."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    proj_a = tmp_path / "projA"
    proj_a.mkdir()
    pid_a = resolve_project_id(str(proj_a))
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, "
                "source_type, confidence, status, project_scope, project_id, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "rule-other", "ruleo1", "git push force remote",
                    "use lease", "correction", "high", "active", "project",
                    "other-proj", now, now,
                ),
            )
        payload = {
            "session_id": "s-empty-formal",
            "prompt": "git push force remote branch",
            "cwd": str(proj_a),
        }
        result = handle(payload, cfg)
        assert result == {"continue": True}
        row = db.fetchone(
            "SELECT cross_project_hits FROM rules WHERE id = 'rule-other'"
        )
        assert row["cross_project_hits"] == 1
        assert pid_a is not None
        assert pid_a != "other-proj"
    finally:
        db.close()


def test_total_rule_count_ignores_archived(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        for i in range(19):
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO rules (id, short_id, trigger_text, action, "
                    "source_type, confidence, status, project_scope, project_id, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"active-{i:02d}", f"a{i:04x}", f"trigger {i}",
                        "act", "correction", "high", "active", "project",
                        "p1", now, now,
                    ),
                )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, "
                "source_type, confidence, status, project_scope, project_id, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "archived-01", "arc001", "archived trigger",
                    "act", "correction", "high", "archived", "project",
                    "p1", now, now,
                ),
            )
        from nokori.db import total_rule_count

        assert total_rule_count(db) == 19
    finally:
        db.close()
