import json
import subprocess
import sys

from nokori import __version__


def _nokori(monkeypatch, tmp_path, *args):
    env = {"NOKORI_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
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
                "install", "health", "maintain", "reset", "export", "import"):
        assert cmd in r.stdout, f"{cmd} missing from --help"


def test_status_on_empty_db(tmp_path, monkeypatch):
    r = _nokori(monkeypatch, tmp_path, "status")
    assert r.returncode == 0, r.stderr
    assert "rules.total    0" in r.stdout
    assert "rules.active   0" in r.stdout


def test_hook_session_start_smoke(tmp_path, monkeypatch):
    env = {"NOKORI_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
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
