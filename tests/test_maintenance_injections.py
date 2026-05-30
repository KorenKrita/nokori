from datetime import datetime, timedelta, timezone

from nokori.config import Config
from nokori.db import open_db
from nokori.lifecycle.maintenance import run_injection_cleanup


def test_injection_cleanup_deletes_old_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        new = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, "
                "source_type, confidence, status, project_scope, project_id, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "r1", "abc123", "t", "a",
                    "correction", "high", "active", "global", None,
                    new, new,
                ),
            )
            tx.execute(
                "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?)",
                ("r1", "s1", "h1", "hot", old),
            )
            tx.execute(
                "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?)",
                ("r1", "s1", "h2", "hot", new),
            )
        deleted = run_injection_cleanup(db)
        assert deleted == 1
        n = db.fetchone("SELECT COUNT(*) AS n FROM injections")["n"]
        assert n == 1
    finally:
        db.close()
