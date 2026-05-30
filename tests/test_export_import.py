import json
import subprocess
import sys


def _run(*args, env_extra=None):
    env = {"PATH": "/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_export_import_roundtrip(tmp_path):
    src_data = tmp_path / "src"
    dst_data = tmp_path / "dst"
    out = tmp_path / "rules.json"

    _run("add", "--trigger", "rule one", "--action", "do x",
         "--source-type", "correction", "--confidence", "high",
         env_extra={"NOKORI_DATA_DIR": str(src_data)})
    _run("add", "--trigger", "rule two", "--action", "do y",
         "--source-type", "preference", "--confidence", "medium",
         env_extra={"NOKORI_DATA_DIR": str(src_data)})

    r = _run("export", str(out), env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r.returncode == 0, r.stderr
    payload = json.loads(out.read_text())
    assert len(payload["rules"]) == 2

    r2 = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(dst_data)})
    assert r2.returncode == 0, r2.stderr
    assert "imported 2" in r2.stdout

    list_out = _run("list", "--all", env_extra={"NOKORI_DATA_DIR": str(dst_data)})
    assert "rule one" in list_out.stdout
    assert "rule two" in list_out.stdout


def test_import_rejects_oversized_trigger(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "huge.json"
    payload = {
        "format": "nokori-export",
        "version": 1,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000001",
                "short_id": "big001",
                "trigger_text": "x" * 20_000,
                "action": "ok",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "trigger_text" in (r.stderr + r.stdout)


def test_import_skips_duplicates(tmp_path):
    src = tmp_path / "src"
    out = tmp_path / "rules.json"
    _run("add", "--trigger", "x", "--action", "y",
         env_extra={"NOKORI_DATA_DIR": str(src)})
    _run("export", str(out), env_extra={"NOKORI_DATA_DIR": str(src)})
    _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(src)})
    second = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(src)})
    assert "skipped 1" in second.stdout
