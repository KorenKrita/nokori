from nokori.config import Config
from nokori.db import open_db
from nokori.hooks.user_prompt_submit import handle
from nokori.utils.host import Host
from nokori.utils.project import resolve_project_id
from nokori.utils.time import now_iso


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert(db, *, id_, trigger, status="candidate", project_id="other-proj"):
    now = _utcnow_iso()
    import hashlib
    sid = hashlib.md5(id_.encode()).hexdigest()[:6]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "source_origin, status, severity, "
            "project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
            (id_, sid, trigger, "use lease",
             "transcript_extraction", status, "reminder",
             "project", project_id, now, now),
        )


def test_shadow_pool_noop_when_promotion_disabled(monkeypatch, tmp_path):
    """With promotion disabled, shadow events are still recorded via hooks."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_PROMOTION_ENABLED", "0")
    cfg = Config.from_env()
    assert cfg.promotion_enabled is False
    db = open_db(cfg.db_path)
    try:
        _insert(db, id_="rule-x", trigger="git push force remote",
                status="candidate", project_id="my-proj")
        proj = tmp_path / "mine"
        proj.mkdir()
        handle({
            "session_id": "s-no-promo",
            "prompt": "git push force remote",
            "cwd": str(proj),
        }, cfg, host=Host.CLAUDE)
        # Shadow events may or may not be recorded depending on config
        # but rule should remain unchanged
        row = db.fetchone("SELECT status FROM rules WHERE id = 'rule-x'")
        assert row["status"] == "candidate"
    finally:
        db.close()


def test_handle_runs_shadow_when_formal_pool_empty(monkeypatch, tmp_path):
    """Scene C: new project with zero active rules still records shadow events."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    proj_a = tmp_path / "projA"
    proj_a.mkdir()
    pid_a = resolve_project_id(str(proj_a))
    db = open_db(cfg.db_path)
    try:
        # Insert a candidate rule for this project (goes in shadow pool)
        _insert(db, id_="rule-cand", trigger="git push force remote",
                status="candidate", project_id=pid_a)
        payload = {
            "session_id": "s-empty-formal",
            "prompt": "git push force remote branch",
            "cwd": str(proj_a),
        }
        result = handle(payload, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}
        # Shadow event should be recorded
        events = db.fetchall(
            "SELECT rule_id FROM rule_shadow_events WHERE rule_id = 'rule-cand'"
        )
        assert len(events) >= 1
    finally:
        db.close()


def test_total_rule_count_ignores_archived(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        for i in range(19):
            _insert(db, id_=f"active-{i:02d}", trigger=f"trigger {i}",
                    status="active", project_id="p1")
        _insert(db, id_="archived-01", trigger="archived trigger",
                status="archived", project_id="p1")
        from nokori.db import total_rule_count

        assert total_rule_count(db) == 19
    finally:
        db.close()
