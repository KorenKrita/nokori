"""End-to-end regressions for v6 autonomous flywheel wiring."""
from __future__ import annotations

import os
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from nokori.config import Config
from nokori.db import SCHEMA_VERSION, dumps_json, fetch_rules, open_db
from nokori.policy import RUNTIME_POLICY_VERSION
from nokori.search.engine import RetrievalEngine
from nokori.hooks.prompt_inject import inject_for_prompt


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _concept(label: str, *, text: str | None = None) -> dict:
    return {
        "id": label,
        "label": label,
        "aliases": [{"text": text or label.replace("_", " "), "strength": "strong"}],
        "match_mode": "phrase",
        "required": True,
    }


def _insert_v6_rule(
    db,
    *,
    short_id: str,
    status: str = "active",
    severity: str = "reminder",
    trigger: str = "danger deploy",
    action: str = "Read the deployment checklist first.",
    concepts: list[dict] | None = None,
    groups: list[dict] | None = None,
    variants: list[dict] | None = None,
    first_observed_useful_at: str | None = None,
):
    rid = str(uuid.uuid4())
    now = _now()
    concepts = concepts if concepts is not None else [_concept("danger_deploy", text=trigger)]
    groups = groups if groups is not None else [{"id": "primary", "all_of": [concepts[0]["id"]]}]
    variants = variants if variants is not None else [{
        "text": trigger,
        "kind": "strong_anchor",
        "requires_concepts": [concepts[0]["id"]],
    }]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, status, severity, "
            "trigger_canonical, concepts, required_concept_groups, trigger_variants, "
            "action_instruction, source_origin, project_scope, first_observed_useful_at, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                short_id,
                SCHEMA_VERSION,
                1,
                "test_v6",
                RUNTIME_POLICY_VERSION,
                status,
                severity,
                trigger,
                dumps_json(concepts),
                dumps_json(groups),
                dumps_json(variants),
                action,
                "transcript_extraction",
                "global",
                first_observed_useful_at,
                now,
                now,
            ),
        )
    return rid


def test_required_concept_miss_blocks_retrieval(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_PROVIDER", "off")
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _insert_v6_rule(
            db,
            short_id="miss01",
            trigger="danger deploy",
            concepts=[_concept("absent_required", text="ABSENT-CONCEPT")],
            groups=[{"id": "primary", "all_of": ["absent_required"]}],
        )
        rules = fetch_rules(db, statuses=("active", "trusted"), global_only=True)

        engine = RetrievalEngine(cfg, db)
        result = engine.retrieve_and_tier("please danger deploy now", rules)

        assert result.hot == []
        assert result.warm == []
    finally:
        db.close()


def test_injection_writes_one_complete_fire_event(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_PROVIDER", "off")
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        rid = _insert_v6_rule(db, short_id="fire01", trigger="danger deploy")

        outcome = inject_for_prompt(
            db,
            cfg,
            session_id="session-fire",
            prompt="danger deploy needs a checklist",
            project_id=None,
        )

        assert outcome is not None
        rows = db.fetchall(
            "SELECT * FROM rule_fire_events WHERE rule_id = ? ORDER BY created_at",
            (rid,),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["injected_rule_version"] == 1
        assert row["injected_trigger_snapshot"] == "danger deploy"
        assert row["injected_action_snapshot"] == "Read the deployment checklist first."
        assert row["decision_features"]
        assert row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
        assert row["bounded_window_ref"]
        assert row["trigger_idf_pool_version"]
        idf_row = db.fetchone(
            "SELECT pool_version FROM trigger_idf_stats WHERE pool_version = ?",
            (row["trigger_idf_pool_version"],),
        )
        assert idf_row is not None
    finally:
        db.close()


def test_cli_add_creates_candidate(tmp_path):
    env = os.environ.copy()
    env["NOKORI_DATA_DIR"] = str(tmp_path)
    env["NOKORI_EMBED_PROVIDER"] = "off"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nokori",
            "add",
            "--trigger",
            "manual dangerous trigger",
            "--action",
            "manual action",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "(candidate)" in result.stdout

    db = open_db(Path(tmp_path) / "rules.db")
    try:
        row = db.fetchone(
            "SELECT schema_version, runtime_policy_version, status, concepts, "
            "required_concept_groups, trigger_variants FROM rules LIMIT 1"
        )
        assert row["schema_version"] == SCHEMA_VERSION
        assert row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
        assert row["status"] == "candidate"
        assert row["concepts"] != "[]"
        assert row["required_concept_groups"] != "[]"
        trigger_variants = json.loads(row["trigger_variants"])
        assert trigger_variants
        assert trigger_variants[0]["text"] == "manual dangerous trigger"
        assert trigger_variants[0]["kind"] == "strong_anchor"
    finally:
        db.close()


def test_cli_edit_rejects_manual_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_PROVIDER", "off")
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _insert_v6_rule(db, short_id="edit01", status="active")
    finally:
        db.close()

    env = os.environ.copy()
    env["NOKORI_DATA_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "nokori", "edit", "edit01", "--status", "trusted"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "trusted" in result.stderr


def test_cli_edit_status_help_exposes_archive_only(tmp_path, monkeypatch):
    env = os.environ.copy()
    env["NOKORI_DATA_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "nokori", "edit", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "{archived}" in result.stdout
    assert "{active,trusted,suppressed,archived}" not in result.stdout
