from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from nokori.config import Config
from nokori.db import SCHEMA_VERSION, dumps_json, fetch_rule_by_short_id, open_db
from nokori.models import Rule
from nokori.policy import RUNTIME_POLICY_VERSION
from nokori.utils.time import normalize_db_timestamp, now_iso


def _cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "0")
    return Config.from_env()


def test_archive_replacement_strength_is_not_downgraded(tmp_path):
    from nokori.archive.fingerprints import (
        STRENGTH_RANK,
        create_archived_fingerprint_from_data,
    )

    assert STRENGTH_RANK["replacement"] > STRENGTH_RANK["system"] > STRENGTH_RANK["user"]

    db = open_db(tmp_path / "rules.db")
    try:
        create_archived_fingerprint_from_data(
            db,
            "rule-replacement",
            "same trigger",
            "same action",
            domain_tags=["python"],
            strength="replacement",
        )
        create_archived_fingerprint_from_data(
            db,
            "rule-user",
            "same trigger",
            "same action",
            domain_tags=["python"],
            strength="user",
        )
        row = db.fetchone("SELECT archive_strength FROM archived_fingerprints")
        assert row["archive_strength"] == "replacement"
    finally:
        db.close()


def test_synthetic_eval_generation_failure_stays_pending(tmp_path, monkeypatch):
    from nokori.cold import stages
    from nokori.cold.stages import CandidateContext, PipelineConfig, run_synthetic_eval

    db = open_db(tmp_path / "rules.db")
    monkeypatch.setattr(stages, "_generate_eval_cases", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad cases")))
    ctx = CandidateContext(
        extractor_output={},
        transcript_ref="transcript",
        source_origin="transcript_extraction",
        project_id=None,
        config=PipelineConfig(None, None, None, None),
        rule_data={"trigger_canonical": "danger", "action_instruction": "be careful"},
        target_status="active",
    )
    try:
        with pytest.raises(ValueError, match="synthetic_eval_generation_failed"):
            run_synthetic_eval(ctx, db, SimpleNamespace())
    finally:
        db.close()


def test_process_candidates_keeps_pending_segments_retryable(tmp_path, monkeypatch):
    from nokori.cold._result import ColdPipelineResult
    from nokori.extract.extractor import Candidate
    from nokori.extract import process

    cfg = _cfg(tmp_path, monkeypatch)
    db = open_db(cfg.db_path)
    monkeypatch.setattr(
        process,
        "run_cold_pipeline",
        lambda *_a, **_k: ColdPipelineResult(status="pending", rule_id=None, rejection_reason="retry", scores=None),
    )
    cand = Candidate("trigger", [], {}, None, "action", None)
    try:
        rules_created, failed, all_ok = process.process_candidates(
            [cand], tmp_path / "session.jsonl", None, cfg, db=db, llm=SimpleNamespace()
        )
        assert rules_created == 0
        assert failed
        assert all_ok is False
        row = db.fetchone("SELECT status FROM transcript_ingest_jobs WHERE segment_hash = ?", (failed[0],))
        assert row["status"] == "pending"
    finally:
        db.close()


def test_process_candidates_does_not_count_merged_as_created(tmp_path, monkeypatch):
    from nokori.cold._result import ColdPipelineResult
    from nokori.extract.extractor import Candidate
    from nokori.extract import process

    cfg = _cfg(tmp_path, monkeypatch)
    db = open_db(cfg.db_path)
    monkeypatch.setattr(
        process,
        "run_cold_pipeline",
        lambda *_a, **_k: ColdPipelineResult(status="merged", rule_id="existing", rejection_reason=None, scores=None),
    )
    cand = Candidate("trigger", [], {}, None, "action", None)
    try:
        rules_created, failed, all_ok = process.process_candidates(
            [cand], tmp_path / "session.jsonl", None, cfg, db=db, llm=SimpleNamespace()
        )
        assert rules_created == 0
        assert failed == []
        assert all_ok is True
    finally:
        db.close()


def test_fire_event_persists_current_project_id(tmp_path):
    from nokori.events.fire import create_fire_event

    db = open_db(tmp_path / "rules.db")
    rule = Rule(
        id="rule-1",
        short_id="r1abcd",
        schema_version=SCHEMA_VERSION,
        rule_version=1,
        created_by_pipeline_version="test",
        runtime_policy_version=RUNTIME_POLICY_VERSION,
        last_rewritten_by_role=None,
        status="trusted",
        severity="reminder",
        trigger_canonical="trigger",
        action_instruction="action",
        project_scope="global",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    try:
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, created_by_pipeline_version, "
                "runtime_policy_version, status, severity, trigger_canonical, action_instruction, "
                "project_scope, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rule.id,
                    rule.short_id,
                    rule.schema_version,
                    rule.rule_version,
                    rule.created_by_pipeline_version,
                    rule.runtime_policy_version,
                    rule.status,
                    rule.severity,
                    rule.trigger_canonical,
                    rule.action_instruction,
                    rule.project_scope,
                    rule.created_at,
                    rule.updated_at,
                ),
            )
        event_id = create_fire_event(
            db,
            rule,
            session_id="s1",
            prompt_hash="p1",
            level="hot",
            decision_features={},
            project_id="project-current",
        )
        row = db.fetchone("SELECT project_id FROM rule_fire_events WHERE id = ?", (event_id,))
        assert row["project_id"] == "project-current"
    finally:
        db.close()


def test_monitor_timestamp_filter_normalizes_frontend_iso():
    expected = datetime(2026, 6, 17, 2, 0, 0, tzinfo=UTC).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert normalize_db_timestamp("2026-06-17T02:00:00Z") == expected


def test_config_from_env_explicit_data_dir_preserves_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("NOKORI_DATA_DIR", raising=False)
    cfg = Config.from_env(data_dir=tmp_path / "runtime-data")
    assert cfg.data_dir == tmp_path / "runtime-data"


def test_logging_configure_updates_level_in_same_process(tmp_path):
    from nokori.utils import logging as nokori_logging

    nokori_logging._configured = False
    nokori_logging._configured_logs_dir = None
    try:
        nokori_logging.configure(tmp_path / "logs", "warn")
        assert logging.getLogger("nokori").level == logging.WARNING
        nokori_logging.configure(tmp_path / "logs", "debug")
        assert logging.getLogger("nokori").level == logging.DEBUG
    finally:
        for handler in list(logging.getLogger("nokori").handlers):
            logging.getLogger("nokori").removeHandler(handler)
        logging.getLogger("nokori").propagate = True
        nokori_logging._configured = False
        nokori_logging._configured_logs_dir = None


def test_excluded_context_merge_keeps_idless_distinct_entries(tmp_path):
    from nokori.cold.integrate import _apply_non_destructive_merge

    db = open_db(tmp_path / "rules.db")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, created_by_pipeline_version, "
            "runtime_policy_version, status, severity, trigger_canonical, action_instruction, "
            "excluded_contexts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rule-ctx",
                "ctx001",
                SCHEMA_VERSION,
                1,
                "test",
                RUNTIME_POLICY_VERSION,
                "active",
                "reminder",
                "trigger",
                "action",
                dumps_json([{"pattern": "explain only"}]),
                now,
                now,
            ),
        )
    try:
        _apply_non_destructive_merge(
            db,
            "rule-ctx",
            {"excluded_contexts": [{"pattern": "dry run"}]},
            "merge_into_existing",
            {},
        )
        row = db.fetchone("SELECT excluded_contexts FROM rules WHERE id = 'rule-ctx'")
        assert [e["pattern"] for e in __import__("json").loads(row["excluded_contexts"])] == [
            "explain only",
            "dry run",
        ]
    finally:
        db.close()


def test_cold_insert_generates_collision_safe_short_id(tmp_path, monkeypatch):
    from nokori.cold import integrate
    from nokori.cold.integrate import insert_rule_from_pipeline

    db = open_db(tmp_path / "rules.db")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, created_by_pipeline_version, "
            "runtime_policy_version, status, severity, trigger_canonical, action_instruction, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("existing", "deadbeef", SCHEMA_VERSION, 1, "test", RUNTIME_POLICY_VERSION, "active", "reminder", "old", "old", now, now),
        )

    class FakeUuid:
        def __str__(self):
            return "deadbeef-0000-4000-8000-000000000000"

    monkeypatch.setattr(integrate.uuid, "uuid4", lambda: FakeUuid())
    try:
        rule_id = insert_rule_from_pipeline(
            db,
            {"trigger_canonical": "new trigger", "action_instruction": "new action"},
            "candidate",
            compiled_matcher=SimpleNamespace(),
        )
        rule = fetch_rule_by_short_id(db, "deadbeef")
        assert rule is not None and rule.id == "existing"
        row = db.fetchone("SELECT short_id FROM rules WHERE id = ?", (rule_id,))
        assert row["short_id"] != "deadbeef"
    finally:
        db.close()
