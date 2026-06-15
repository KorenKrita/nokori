import subprocess
import sys
from datetime import datetime, timezone

from nokori.config import Config
from nokori.db import open_db


def _run(*args, env_extra=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "NOKORI_EMBED_ENABLED": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_dismiss_archives_directly(tmp_path, monkeypatch):
    """Dismiss no longer requires a recent injection - archives directly."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    r = _run(
        "add",
        "--trigger",
        "never force push",
        "--action",
        "use lease",
        env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
    )
    assert r.returncode == 0, r.stderr
    short = r.stdout.split()[1]
    r2 = _run("dismiss", short, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert r2.returncode == 0
    assert "archived" in r2.stdout


def test_cli_dismiss_after_fire_event(tmp_path, monkeypatch):
    """Dismiss works when fire events exist for the rule."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    env = {"NOKORI_DATA_DIR": str(tmp_path)}
    r = _run(
        "add",
        "--trigger",
        "never force push",
        "--action",
        "use lease",
        "--variants",
        "git push --force",
        env_extra=env,
    )
    short = r.stdout.split()[1]
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z",
        )
        row = db.fetchone("SELECT id FROM rules WHERE short_id = ?", (short,))
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-1", row["id"], "sess-cli", "ph1", "hot", now),
            )
    finally:
        db.close()
    r2 = _run("dismiss", short, env_extra=env)
    assert r2.returncode == 0, r2.stderr + r2.stdout
