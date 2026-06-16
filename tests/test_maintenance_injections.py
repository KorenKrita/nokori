from datetime import UTC, datetime, timedelta

from nokori.config import Config
from nokori.db import open_db
from nokori.lifecycle.maintenance import run_injection_cleanup


def test_injection_cleanup_deletes_old_rows(monkeypatch, tmp_path):
    """run_injection_cleanup now deletes old fire events."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        new = datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "source_origin, status, severity, "
                "project_scope, project_id, created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
                (
                    "r1", "abc123", "t", "a",
                    "transcript_extraction", "active", "reminder",
                    "global", None, new, new,
                ),
            )
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-old", "r1", "s1", "h1", "hot", old),
            )
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-new", "r1", "s1", "h2", "hot", new),
            )
        deleted = run_injection_cleanup(db)
        assert deleted == 1
        n = db.fetchone("SELECT COUNT(*) AS n FROM rule_fire_events")["n"]
        assert n == 1
    finally:
        db.close()
