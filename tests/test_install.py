import json
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


def test_install_omp_dry_run_shows_diff(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    ext = omp_home / "extensions" / "nokori.ts"
    r = _run(
        "install", "--omp", "--dry-run",
        env_extra={
            "NOKORI_DATA_DIR": str(data),
            "NOKORI_OMP_HOME": str(omp_home),
        },
    )
    assert r.returncode == 0, r.stderr
    assert str(ext) in r.stdout
    assert "spawnSync" in r.stdout
    assert 'input: JSON.stringify(payload)' in r.stdout
    assert 'import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent/extensibility/extensions"' in r.stdout
    assert 'import type HookAPI from "@oh-my-pi/pi-coding-agent/extensibility/hooks"' not in r.stdout
    assert 'export default function nokori(pi: ExtensionAPI): void {' in r.stdout
    assert 'pi.on("session_start"' in r.stdout
    assert 'pi.on("before_agent_start"' in r.stdout
    assert 'pi.on("tool_call"' in r.stdout
    assert 'pi.on("session_shutdown"' in r.stdout
    assert 'session_' + 'stop' not in r.stdout
    assert 'runNokori("session-end", buildCommonPayload(ctx), 2_000);' in r.stdout
    assert 'timeout: timeoutMs' in r.stdout
    assert 'event.session_id' not in r.stdout
    assert 'event.session_file' not in r.stdout
    assert 'continue: true' not in r.stdout
    assert " as HookAPI" not in r.stdout
    assert not ext.exists()


def test_install_omp_writes_extension(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    ext = omp_home / "extensions" / "nokori.ts"
    r = _run(
        "install", "--omp",
        env_extra={
            "NOKORI_DATA_DIR": str(data),
            "NOKORI_OMP_HOME": str(omp_home),
        },
    )
    assert r.returncode == 0, r.stderr
    assert "Recorded install platforms: omp" in r.stdout
    assert ext.exists()
    text = ext.read_text()
    assert 'import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent/extensibility/extensions"' in text
    assert text.startswith("// @generated by nokori install --omp; do not edit manually.\n")
    assert 'import type HookAPI from "@oh-my-pi/pi-coding-agent/extensibility/hooks"' not in text
    assert 'export default function nokori(pi: ExtensionAPI): void {' in text
    assert f'spawnSync({json.dumps(sys.executable)}, ["-I", "-m", "nokori", "hook", event]' in text
    assert 'input: JSON.stringify(payload)' in text
    assert 'env: { ...process.env, NOKORI_HOST: "omp" }' in text
    assert 'customType: "nokori"' in text
    assert 'details: { source: "nokori" }' in text
    assert text.count('attribution: "agent"') == 2
    assert 'tool_name: event.toolName' in text
    assert 'tool_input: event.input' in text
    assert 'pi.on("session_shutdown"' in text
    assert 'session_' + 'stop' not in text
    assert 'runNokori("session-end", buildCommonPayload(ctx), 2_000);' in text
    assert 'timeout: timeoutMs' in text
    assert 'runNokori("session-start", buildCommonPayload(ctx), 5_000);' in text
    assert 'runNokori("pre-tool-use", {' in text
    assert '}, 5_000);' in text
    assert 'event.session_id' not in text
    assert 'event.session_file' not in text
    assert 'continue: true' not in text
    assert " as HookAPI" not in text


def test_install_omp_idempotent(tmp_path):
    omp_home = tmp_path / "omp-agent"
    env = {
        "NOKORI_DATA_DIR": str(tmp_path / "data"),
        "NOKORI_OMP_HOME": str(omp_home),
    }
    _run("install", "--omp", env_extra=env)
    ext = omp_home / "extensions" / "nokori.ts"
    before = ext.read_text()
    r = _run("install", "--omp", env_extra=env)
    after = ext.read_text()
    assert r.returncode == 0, r.stderr
    assert "no changes needed" in r.stdout
    assert before == after
    assert after.count('pi.on("tool_call"') == 1
    assert after.count('pi.on("session_shutdown"') == 1
    assert 'session_' + 'stop' not in after


def test_install_omp_refuses_to_overwrite_unmanaged_extension(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    ext = omp_home / "extensions" / "nokori.ts"
    ext.parent.mkdir(parents=True)
    ext.write_text("export default function customExtension() {}\n")

    r = _run(
        "install", "--omp",
        env_extra={
            "NOKORI_DATA_DIR": str(data),
            "NOKORI_OMP_HOME": str(omp_home),
        },
    )

    assert r.returncode == 1
    assert "refusing to overwrite unmanaged OMP extension" in r.stderr
    assert ext.read_text() == "export default function customExtension() {}\n"
    assert not (data / "install_targets.json").exists()


def test_uninstall_omp_removes_extension(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    env = {
        "NOKORI_DATA_DIR": str(data),
        "NOKORI_OMP_HOME": str(omp_home),
    }
    _run("install", "--omp", env_extra=env)
    ext = omp_home / "extensions" / "nokori.ts"
    assert ext.exists()
    r = _run("install", "--uninstall", "--omp", env_extra=env)
    assert r.returncode == 0, r.stderr
    assert not ext.exists()
    targets = json.loads((data / "install_targets.json").read_text())
    assert targets["platforms"] == []
    assert "Recorded install platforms: (none)" in r.stdout


def test_uninstall_omp_refuses_to_remove_unmanaged_extension(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    env = {
        "NOKORI_DATA_DIR": str(data),
        "NOKORI_OMP_HOME": str(omp_home),
    }
    installed = _run("install", "--omp", env_extra=env)
    assert installed.returncode == 0, installed.stderr
    ext = omp_home / "extensions" / "nokori.ts"
    ext.write_text("export default function customExtension() {}\n")

    r = _run("install", "--uninstall", "--omp", env_extra=env)

    assert r.returncode == 1
    assert "refusing to remove unmanaged OMP extension" in r.stderr
    assert ext.read_text() == "export default function customExtension() {}\n"
    targets = json.loads((data / "install_targets.json").read_text())
    assert targets["platforms"] == ["omp"]


def test_health_reports_omp_bridge(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    env = {
        "NOKORI_DATA_DIR": str(data),
        "NOKORI_OMP_HOME": str(omp_home),
    }
    installed = _run("install", "--omp", env_extra=env)
    assert installed.returncode == 0, installed.stderr

    health = _run("health", env_extra=env)

    assert health.returncode == 0, health.stderr
    assert "hooks.omp" in health.stdout
    assert "registered" in health.stdout


def test_health_warns_when_generated_omp_bridge_is_stale(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    env = {
        "NOKORI_DATA_DIR": str(data),
        "NOKORI_OMP_HOME": str(omp_home),
    }
    installed = _run("install", "--omp", env_extra=env)
    assert installed.returncode == 0, installed.stderr
    ext = omp_home / "extensions" / "nokori.ts"
    ext.write_text(ext.read_text() + "// stale\n")

    health = _run("health", env_extra=env)

    assert health.returncode == 0, health.stderr
    assert "hooks.omp" in health.stdout
    assert "warn" in health.stdout
    assert "stale" in health.stdout
    assert "nokori install --omp" in health.stdout


def test_install_omp_records_platform_only(tmp_path):
    data = tmp_path / "data"
    omp_home = tmp_path / "omp-agent"
    claude_home = tmp_path / "claude"
    cursor_home = tmp_path / "cursor"
    r = _run(
        "install", "--omp",
        env_extra={
            "NOKORI_DATA_DIR": str(data),
            "NOKORI_OMP_HOME": str(omp_home),
            "NOKORI_CLAUDE_HOME": str(claude_home),
            "NOKORI_CURSOR_HOME": str(cursor_home),
        },
    )
    assert r.returncode == 0, r.stderr
    assert "Recorded install platforms: omp" in r.stdout
    targets = json.loads((data / "install_targets.json").read_text())
    assert targets["platforms"] == ["omp"]
    assert not (claude_home / "settings.json").exists()
    assert not (cursor_home / "hooks.json").exists()


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
