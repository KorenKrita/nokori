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


def _v6_structure(trigger: str):
    return {
        "concepts": [
            {
                "id": "manual_trigger",
                "label": trigger,
                "aliases": [{"text": trigger, "strength": "strong"}],
                "match_mode": "phrase",
                "required": True,
            }
        ],
        "required_concept_groups": [
            {"id": "manual_primary", "all_of": ["manual_trigger"]}
        ],
        "excluded_contexts": [],
        "trigger_variants": [
            {
                "text": trigger,
                "kind": "strong_anchor",
                "requires_concepts": ["manual_trigger"],
            }
        ],
    }


def test_export_import_roundtrip(tmp_path):
    src_data = tmp_path / "src"
    dst_data = tmp_path / "dst"
    out = tmp_path / "rules.json"

    _run("add", "--trigger", "rule one", "--action", "do x",
         env_extra={"NOKORI_DATA_DIR": str(src_data)})
    _run("add", "--trigger", "rule two", "--action", "do y",
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


def test_export_includes_v6_matcher_structure(tmp_path):
    src_data = tmp_path / "src"
    out = tmp_path / "rules.json"

    r_add = _run("add", "--trigger", "rule one", "--action", "do x",
                 env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r_add.returncode == 0, r_add.stderr

    r = _run("export", str(out), env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r.returncode == 0, r.stderr

    payload = json.loads(out.read_text())
    rule = payload["rules"][0]
    assert rule["schema_version"] == 6
    assert rule["runtime_policy_version"] == "1.0.0"
    assert rule["concepts"]
    assert rule["required_concept_groups"]
    assert "excluded_contexts" in rule
    assert rule["trigger_variants"]
    assert rule["trigger_variants"][0]["text"] == "rule one"
    assert rule["trigger_variants"][0]["kind"] == "strong_anchor"


def test_export_coerces_json_fields_to_expected_container_types(tmp_path):
    from nokori.db import open_db

    src_data = tmp_path / "src"
    out = tmp_path / "rules.json"

    r_add = _run("add", "--trigger", "rule one", "--action", "do x",
                 env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r_add.returncode == 0, r_add.stderr

    db = open_db(src_data / "rules.db")
    try:
        with db.transaction() as tx:
            tx.execute("UPDATE rules SET concepts = '{}', search_terms = '[]'")
    finally:
        db.close()

    r = _run("export", str(out), env_extra={"NOKORI_DATA_DIR": str(src_data)})
    assert r.returncode == 0, r.stderr

    rule = json.loads(out.read_text())["rules"][0]
    assert rule["concepts"] == []
    assert rule["search_terms"] == {}


def test_import_rejects_active_rule_without_v6_matcher_structure(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "unsafe.json"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000111",
                "short_id": "bad111",
                "trigger_canonical": "force push shared branch",
                "action_instruction": "use --force-with-lease",
                "status": "active",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")

    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})

    assert r.returncode != 0
    assert "required_concept_groups" in (r.stderr + r.stdout)


def test_import_rejects_active_rule_without_trigger_variants(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "unsafe_no_variants.json"
    trigger = "force push shared branch"
    structure = _v6_structure(trigger)
    structure.pop("trigger_variants")
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000113",
                "short_id": "bad113",
                "trigger_canonical": trigger,
                **structure,
                "action_instruction": "use --force-with-lease",
                "status": "active",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")

    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})

    assert r.returncode != 0
    assert "trigger_variants" in (r.stderr + r.stdout)


def test_import_forces_current_schema_policy_and_keeps_structure(tmp_path):
    from nokori.db import open_db

    data = tmp_path / "data"
    out = tmp_path / "legacy_meta.json"
    trigger = "force push shared branch"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000112",
                "short_id": "bad112",
                "schema_version": 1,
                "runtime_policy_version": "policy_v1",
                "trigger_canonical": trigger,
                **_v6_structure(trigger),
                "action_instruction": "use --force-with-lease",
                "status": "candidate",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")

    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})

    assert r.returncode == 0, r.stderr
    db = open_db(data / "rules.db")
    try:
        row = db.fetchone(
            "SELECT schema_version, runtime_policy_version, concepts, "
            "required_concept_groups FROM rules WHERE short_id = ?",
            ("bad112",),
        )
    finally:
        db.close()
    assert row["schema_version"] == 6
    assert row["runtime_policy_version"] == "1.0.0"
    assert json.loads(row["concepts"])
    assert json.loads(row["required_concept_groups"])


def test_import_rehydrates_formal_rule_as_external_candidate(tmp_path):
    from nokori.db import open_db

    data = tmp_path / "data"
    out = tmp_path / "formal_rule.json"
    trigger = "force push shared branch"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000114",
                "short_id": "abc114",
                "trigger_canonical": trigger,
                **_v6_structure(trigger),
                "action_instruction": "use --force-with-lease",
                "status": "trusted",
                "source_origin": "transcript_extraction",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")

    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})

    assert r.returncode == 0, r.stderr
    db = open_db(data / "rules.db")
    try:
        row = db.fetchone(
            "SELECT status, source_origin, activation_origin FROM rules WHERE short_id = ?",
            ("abc114",),
        )
    finally:
        db.close()
    assert row["status"] == "candidate"
    assert row["source_origin"] == "external_source_material"
    assert row["activation_origin"] is None


def test_import_preserves_archived_negative_memory(tmp_path):
    from nokori.db import open_db

    data = tmp_path / "data"
    out = tmp_path / "archived_rule.json"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000115",
                "short_id": "abc115",
                "trigger_canonical": "old unsafe rule",
                "action_instruction": "do not restore",
                "status": "archived",
                "source_origin": "transcript_extraction",
                "archived_reason": "user_archive",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")

    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})

    assert r.returncode == 0, r.stderr
    db = open_db(data / "rules.db")
    try:
        row = db.fetchone(
            "SELECT status, source_origin, archived_reason FROM rules WHERE short_id = ?",
            ("abc115",),
        )
    finally:
        db.close()
    assert row["status"] == "archived"
    assert row["source_origin"] == "transcript_extraction"
    assert row["archived_reason"] == "user_archive"


def test_import_rejects_oversized_trigger(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "huge.json"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000001",
                "short_id": "big001",
                "trigger_canonical": "x" * 20_000,
                "action_instruction": "ok",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "trigger_canonical" in (r.stderr + r.stdout)


def test_import_rejects_invalid_status(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad.json"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "short_id": "bad001",
                "trigger_canonical": "t",
                "action_instruction": "a",
                "status": "not_a_real_status",
            }
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    r = _run("import", str(out), env_extra={"NOKORI_DATA_DIR": str(data)})
    assert r.returncode != 0
    assert "status" in (r.stderr + r.stdout)


def test_import_rejects_non_uuid_id(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad_id.json"
    payload = {
        "format": "nokori-export",
        "version": 6,
        "rules": [
            {
                "id": "my-rule-1",
                "trigger_canonical": "t",
                "action_instruction": "a",
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
        "version": 6,
        "rules": [
                {
                    "id": "00000000-0000-4000-8000-000000000099",
                    "short_id": "a0b1c2",
                    "trigger_canonical": "Force push to shared branch",
                    "trigger_canonical_zh": "strong push to shared branch zh",
                    **_v6_structure("Force push to shared branch"),
                    "trigger_variants": ["git push --force"],
                    "search_terms": {"en": ["force", "push"], "zh": ["force-zh"]},
                    "action_instruction": "use --force-with-lease",
                "action_instruction_zh": "use lease zh",
                "status": "active",
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
    assert rule["trigger_canonical_zh"] == "strong push to shared branch zh"
    assert rule["action_instruction_zh"] == "use lease zh"

    r3 = _run("import", str(export_out), env_extra={"NOKORI_DATA_DIR": str(dst_data)})
    assert r3.returncode == 0, r3.stderr
    assert "imported 1" in r3.stdout
