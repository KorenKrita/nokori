import json
import os
import subprocess
import sys


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


def test_install_dry_run_shows_diff(tmp_path):
    home = tmp_path / "claude"
    r = _run(
        "install", "--dry-run",
        env_extra={"NOKORI_DATA_DIR": str(tmp_path / "data"),
                   "NOKORI_CLAUDE_HOME": str(home)},
    )
    assert r.returncode == 0, r.stderr
    assert "SessionStart" in r.stdout
    assert "UserPromptSubmit" in r.stdout
    assert "PreToolUse" in r.stdout
    assert "SessionEnd" in r.stdout
    # No file written yet
    assert not (home / "settings.json").exists()


def test_install_writes_settings(tmp_path):
    home = tmp_path / "claude"
    r = _run(
        "install",
        env_extra={"NOKORI_DATA_DIR": str(tmp_path / "data"),
                   "NOKORI_CLAUDE_HOME": str(home)},
    )
    assert r.returncode == 0, r.stderr
    settings = home / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    assert "hooks" in data
    for evt in ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd"):
        assert evt in data["hooks"]
        spec = data["hooks"][evt]
        assert any("nokori" in h["command"] for entry in spec for h in entry["hooks"])


def test_install_merges_with_existing(tmp_path):
    home = tmp_path / "claude"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"matcher": "*", "hooks": [
                    {"type": "command", "command": "/bin/echo other", "timeout": 1}
                ]}
            ]
        }
    }))
    r = _run(
        "install",
        env_extra={"NOKORI_DATA_DIR": str(tmp_path / "data"),
                   "NOKORI_CLAUDE_HOME": str(home)},
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(settings.read_text())
    cmds = [h["command"] for entry in data["hooks"]["SessionStart"] for h in entry["hooks"]]
    assert "/bin/echo other" in cmds
    assert any("nokori" in c for c in cmds)


def test_install_idempotent(tmp_path):
    home = tmp_path / "claude"
    env = {"NOKORI_DATA_DIR": str(tmp_path / "data"),
           "NOKORI_CLAUDE_HOME": str(home)}
    _run("install", env_extra=env)
    before = (home / "settings.json").read_text()
    _run("install", env_extra=env)
    after = (home / "settings.json").read_text()
    data_before = json.loads(before)
    data_after = json.loads(after)
    for evt in data_before["hooks"]:
        nokori_count = sum(
            1 for entry in data_after["hooks"][evt]
            for h in entry["hooks"] if "nokori" in h["command"]
        )
        assert nokori_count == 1, f"{evt} has {nokori_count} nokori entries"


def test_uninstall_removes_only_nokori(tmp_path):
    home = tmp_path / "claude"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"matcher": "*", "hooks": [
                    {"type": "command", "command": "/bin/echo keep", "timeout": 1}
                ]}
            ]
        }
    }))
    env = {"NOKORI_DATA_DIR": str(tmp_path / "data"),
           "NOKORI_CLAUDE_HOME": str(home)}
    _run("install", env_extra=env)
    r = _run("install", "--uninstall", env_extra=env)
    assert r.returncode == 0, r.stderr
    data = json.loads(settings.read_text())
    cmds = [h["command"] for entry in data["hooks"]["SessionStart"] for h in entry["hooks"]]
    assert "/bin/echo keep" in cmds
    assert not any("nokori" in c for c in cmds)


def test_disable_enable(tmp_path):
    home = tmp_path / "claude"
    env = {"NOKORI_DATA_DIR": str(tmp_path / "data"),
           "NOKORI_CLAUDE_HOME": str(home)}
    _run("install", env_extra=env)
    _run("install", "--disable", env_extra=env)
    data = json.loads((home / "settings.json").read_text())
    assert data.get("env", {}).get("NOKORI_DISABLED") == "1"
    _run("install", "--enable", env_extra=env)
    data = json.loads((home / "settings.json").read_text())
    assert "NOKORI_DISABLED" not in data.get("env", {})
