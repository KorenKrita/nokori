import json
import os
import subprocess
import sys


def _run(*args, env_extra=None):
    env = {"PATH": "/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    cmd = list(args)
    if cmd and cmd[0] == "install" and "--no-prefetch-embed" not in cmd:
        cmd = cmd + ["--no-prefetch-embed"]
    return subprocess.run(
        [sys.executable, "-m", "nokori", *cmd],
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
    assert "project-level" not in r.stdout
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
    assert "project-level" not in r.stdout
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


def test_install_cursor_only(tmp_path):
    cursor_home = tmp_path / "cursor"
    r = _run(
        "install", "--cursor",
        env_extra={
            "NOKORI_DATA_DIR": str(tmp_path / "data"),
            "NOKORI_CURSOR_HOME": str(cursor_home),
        },
    )
    assert r.returncode == 0, r.stderr
    assert "project-level" not in r.stdout
    hooks = cursor_home / "hooks.json"
    assert hooks.exists()
    data = json.loads(hooks.read_text())
    assert data.get("version") == 1
    for evt in ("sessionStart", "beforeSubmitPrompt", "preToolUse", "sessionEnd"):
        assert evt in data["hooks"]
        assert any("nokori" in e["command"] for e in data["hooks"][evt])
    pre = data["hooks"]["preToolUse"]
    nokori_pre = next(e for e in pre if "nokori" in e["command"])
    assert "Shell" in nokori_pre.get("matcher", "")
    bsp = next(e for e in data["hooks"]["beforeSubmitPrompt"] if "nokori" in e["command"])
    assert "matcher" not in bsp


def test_install_all_targets_shows_duplicate_note(tmp_path):
    claude_home = tmp_path / "claude"
    cursor_home = tmp_path / "cursor"
    data = tmp_path / "data"
    r = _run(
        "install", "--all",
        env_extra={
            "NOKORI_DATA_DIR": str(data),
            "NOKORI_CLAUDE_HOME": str(claude_home),
            "NOKORI_CURSOR_HOME": str(cursor_home),
        },
    )
    assert r.returncode == 0, r.stderr
    assert "Installed for BOTH" in r.stdout
    assert "Recorded install platforms: claude,cursor" in r.stdout
    assert (claude_home / "settings.json").exists()
    assert (cursor_home / "hooks.json").exists()
    targets = json.loads((data / "install_targets.json").read_text())
    assert targets["platforms"] == ["claude", "cursor"]


def test_install_cursor_records_platform_only(tmp_path):
    data = tmp_path / "data"
    cursor_home = tmp_path / "cursor"
    r = _run(
        "install", "--cursor",
        env_extra={
            "NOKORI_DATA_DIR": str(data),
            "NOKORI_CURSOR_HOME": str(cursor_home),
        },
    )
    assert r.returncode == 0, r.stderr
    assert "Recorded install platforms: cursor" in r.stdout
    assert "project-level" not in r.stdout


def test_disable_enable(tmp_path):
    home = tmp_path / "claude"
    env = {"NOKORI_DATA_DIR": str(tmp_path / "data"),
           "NOKORI_CLAUDE_HOME": str(home)}
    _run("install", env_extra=env)
    r = _run("install", "--disable", env_extra=env)
    assert r.returncode == 0, r.stderr
    assert "NOKORI_DISABLED=1" in r.stdout or "settings.json env" in r.stdout
    data = json.loads((home / "settings.json").read_text())
    assert data.get("env", {}).get("NOKORI_DISABLED") == "1"
    _run("install", "--enable", env_extra=env)
    data = json.loads((home / "settings.json").read_text())
    assert "NOKORI_DISABLED" not in data.get("env", {})


def test_disable_only_cursor_errors(tmp_path):
    cursor_home = tmp_path / "cursor"
    r = _run(
        "install", "--disable", "--cursor",
        env_extra={
            "NOKORI_DATA_DIR": str(tmp_path / "data"),
            "NOKORI_CURSOR_HOME": str(cursor_home),
        },
    )
    assert r.returncode != 0
    assert "only" in r.stderr.lower() or "Claude" in r.stderr


def test_disable_with_cursor_installed_prints_hint(tmp_path):
    data_dir = tmp_path / "data"
    claude_home = tmp_path / "claude"
    cursor_home = tmp_path / "cursor"
    env = {
        "NOKORI_DATA_DIR": str(data_dir),
        "NOKORI_CLAUDE_HOME": str(claude_home),
        "NOKORI_CURSOR_HOME": str(cursor_home),
    }
    _run("install", "--all", env_extra=env)
    r = _run("install", "--disable", env_extra=env)
    assert r.returncode == 0, r.stderr
    assert "uninstall --cursor" in r.stdout
