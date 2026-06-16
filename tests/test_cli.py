import json
import subprocess
import sys
from datetime import UTC

from nokori import __version__


def _nokori(monkeypatch, tmp_path, *args):
    env = {
        "NOKORI_DATA_DIR": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "NOKORI_EMBED_ENABLED": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_version(tmp_path, monkeypatch):
    r = _nokori(monkeypatch, tmp_path, "--version")
    assert r.returncode == 0
    assert __version__ in r.stdout


def test_help_lists_subcommands(tmp_path, monkeypatch):
    r = _nokori(monkeypatch, tmp_path, "--help")
    assert r.returncode == 0
    for cmd in ("add", "list", "show", "dismiss", "test", "extract", "status",
                "install", "health", "maintain", "export", "import"):
        assert cmd in r.stdout, f"{cmd} missing from --help"
    assert "reset" not in r.stdout


def test_status_on_empty_db(tmp_path, monkeypatch):
    r = _nokori(monkeypatch, tmp_path, "status")
    assert r.returncode == 0, r.stderr
    assert "rules.total" in r.stdout
    assert "rules.active" in r.stdout
    assert "hooks.claude.installed" in r.stdout
    assert "hooks.cursor.installed" in r.stdout


def test_status_claude_disabled_flag(tmp_path, monkeypatch):
    claude_home = tmp_path / "claude"
    env_extra = {
        "NOKORI_DATA_DIR": str(tmp_path),
        "NOKORI_CLAUDE_HOME": str(claude_home),
    }
    from tests.test_install import _run

    _run("install", env_extra=env_extra)
    _run("install", "--disable", env_extra=env_extra)
    env = {"NOKORI_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin", **env_extra}
    r = subprocess.run(
        [sys.executable, "-m", "nokori", "status"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "hooks.claude.disabled  yes" in r.stdout


def test_status_shows_rule_counts(tmp_path, monkeypatch):
    """Status shows rule counts after inserting a rule."""
    from datetime import datetime

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.config import Config
    from nokori.db import open_db

    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?)",
                (
                    "rule-A", "rulea1",
                    "git push force remote", "use lease",
                    "active", "reminder", "project", "proj-A",
                    now, now,
                ),
            )
    finally:
        db.close()

    r = _nokori(monkeypatch, tmp_path, "status")
    assert r.returncode == 0, r.stderr
    # Should show at least 1 active rule
    assert "rules.active" in r.stdout and "1" in r.stdout


def test_edit_rejects_manual_gate_eligible_severity(tmp_path, monkeypatch):
    """CLI edit must not manually make rules Gate-capable."""
    from datetime import datetime

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.config import Config
    from nokori.db import open_db

    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?)",
                (
                    "rule-gate", "gate01",
                    "dangerous deploy command", "ask before deploy",
                    "trusted", "reminder", "global", None,
                    now, now,
                ),
            )
    finally:
        db.close()

    r = _nokori(
        monkeypatch, tmp_path, "edit", "gate01", "--severity", "gate_eligible"
    )

    assert r.returncode != 0
    assert "gate_eligible" in (r.stderr + r.stdout)
    db = open_db(cfg.db_path)
    try:
        row = db.fetchone("SELECT severity FROM rules WHERE short_id = ?", ("gate01",))
        assert row["severity"] == "reminder"
    finally:
        db.close()


def test_hook_session_start_smoke(tmp_path, monkeypatch):
    env = {"NOKORI_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin",
           "NOKORI_EMBED_ENABLED": "0", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "nokori", "hook", "session-start"],
        input=json.dumps({"session_id": "s1", "cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out.get("continue") is True


def test_hook_disabled_short_circuits(tmp_path):
    env = {
        "NOKORI_DATA_DIR": str(tmp_path),
        "NOKORI_DISABLED": "1",
        "PATH": "/usr/bin:/bin",
        "NOKORI_EMBED_ENABLED": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "nokori", "hook", "user-prompt-submit"],
        input="{}",
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout.strip()) == {"continue": True}
