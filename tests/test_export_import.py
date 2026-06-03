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
        "version": 2,
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


def test_import_rejects_invalid_source_type(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad.json"
    payload = {
        "format": "nokori-export",
        "version": 2,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "short_id": "bad001",
                "trigger_text": "t",
                "action": "a",
                "source_type": "not_a_real_type",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "source_type" in (r.stderr + r.stdout)


def test_import_rejects_non_integer_evidence_score(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad_score.json"
    payload = {
        "format": "nokori-export",
        "version": 2,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000003",
                "short_id": "abc123",
                "trigger_text": "t",
                "action": "a",
                "evidence_score": "high",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "evidence_score" in (r.stderr + r.stdout)


def test_import_rejects_non_uuid_id(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad_id.json"
    payload = {
        "format": "nokori-export",
        "version": 2,
        "rules": [
            {
                "id": "my-rule-1",
                "trigger_text": "t",
                "action": "a",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "UUID" in (r.stderr + r.stdout) or "uuid" in (r.stderr + r.stdout).lower()


def test_import_skips_duplicates(tmp_path):
    src = tmp_path / "src"
    out = tmp_path / "rules.json"
    _run("add", "--trigger", "xxx", "--action", "yyy",
         env_extra={"NOKORI_DATA_DIR": str(src)})
    _run("export", str(out), env_extra={"NOKORI_DATA_DIR": str(src)})
    _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(src)})
    second = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(src)})
    assert "skipped 1" in second.stdout


def test_export_import_roundtrip_zh_fields(tmp_path):
    """_zh fields survive export -> import round-trip."""
    src_data = tmp_path / "src"
    dst_data = tmp_path / "dst"
    out = tmp_path / "rules_zh.json"

    payload = {
        "format": "nokori-export",
        "version": 2,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000099",
                "short_id": "a0b1c2",
                "trigger_text": "Force push to shared branch",
                "trigger_variants": ["git push --force"],
                "search_terms": {"en": ["force", "push"], "zh": ["强推"]},
                "behavior": "git push --force",
                "action": "use --force-with-lease",
                "rationale": "force push overwrites peers work",
                "source_type": "correction",
                "confidence": "high",
                "status": "active",
                "trigger_text_zh": "强制推送到共享分支",
                "behavior_zh": "使用 git push --force",
                "action_zh": "使用 --force-with-lease",
                "rationale_zh": "强推会覆盖同事的工作",
            }
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r.returncode == 0, r.stderr
    assert "imported 1" in r.stdout

    export_out = tmp_path / "exported.json"
    r2 = _run("export", str(export_out), env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r2.returncode == 0, r2.stderr

    exported = json.loads(export_out.read_text(encoding="utf-8"))
    assert len(exported["rules"]) == 1
    rule = exported["rules"][0]
    assert rule["trigger_text_zh"] == "强制推送到共享分支"
    assert rule["behavior_zh"] == "使用 git push --force"
    assert rule["action_zh"] == "使用 --force-with-lease"
    assert rule["rationale_zh"] == "强推会覆盖同事的工作"

    r3 = _run("import", str(export_out), env_extra={"NOKORI_DATA_DIR": str(dst_data)})
    assert r3.returncode == 0, r3.stderr
    assert "imported 1" in r3.stdout

    final_out = tmp_path / "final.json"
    r4 = _run("export", str(final_out), env_extra={"NOKORI_DATA_DIR": str(dst_data)})
    assert r4.returncode == 0, r4.stderr
    final = json.loads(final_out.read_text(encoding="utf-8"))
    final_rule = final["rules"][0]
    assert final_rule["trigger_text_zh"] == "强制推送到共享分支"
    assert final_rule["behavior_zh"] == "使用 git push --force"
    assert final_rule["action_zh"] == "使用 --force-with-lease"
    assert final_rule["rationale_zh"] == "强推会覆盖同事的工作"
