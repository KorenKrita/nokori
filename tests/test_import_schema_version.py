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
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_import_rejects_wrong_schema_version(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad.json"
    payload = {
        "format": "nokori-export",
        "version": 1,
        "rules": [],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "schema version" in (r.stderr + r.stdout).lower()
