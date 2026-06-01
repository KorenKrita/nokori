import subprocess
import sys
from datetime import datetime, timezone

import pytest

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


def test_cli_dismiss_requires_recent_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    r = _run(
        "add",
        "--trigger",
        "never force push",
        "--action",
        "use lease",
        "--source-type",
        "correction",
        "--confidence",
        "high",
        env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
    )
    assert r.returncode == 0, r.stderr
    short = r.stdout.split()[1]
    r2 = _run("dismiss", short, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert r2.returncode != 0
    assert "24 hours" in (r2.stderr + r2.stdout)


def test_cli_dismiss_after_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    env = {"NOKORI_DATA_DIR": str(tmp_path)}
    r = _run(
        "add",
        "--trigger",
        "never force push",
        "--action",
        "use lease",
        "--source-type",
        "correction",
        "--confidence",
        "high",
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
                "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?)",
                (row["id"], "sess-cli", "ph1", "hot", now),
            )
    finally:
        db.close()
    r2 = _run("dismiss", short, env_extra=env)
    assert r2.returncode == 0, r2.stderr + r2.stdout
