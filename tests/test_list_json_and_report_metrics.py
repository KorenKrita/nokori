"""Tests for nokori list --json and nokori report --metrics."""
from __future__ import annotations

import argparse
import json
import uuid

import pytest

from nokori.commands.list_rules import run as run_list
from nokori.commands.report import run as run_report
from nokori.config import Config
from nokori.db import open_db
from nokori.utils.time import now_iso


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    return Config.from_env()


def _insert_rule(db, short_id: str, status: str = "active", severity: str = "reminder",
                 trigger: str = "test trigger", project_id: str | None = None) -> str:
    rule_id = str(uuid.uuid4())
    now = now_iso()
    db.conn.execute(
        "INSERT INTO rules (id, short_id, schema_version, rule_version, status, severity, "
        "trigger_canonical, action_instruction, project_scope, project_id, created_at, updated_at) "
        "VALUES (?, ?, 6, 1, ?, ?, ?, 'do something', ?, ?, ?, ?)",
        (rule_id, short_id, status, severity, trigger,
         "project" if project_id else "global", project_id, now, now),
    )
    db.conn.commit()
    return rule_id


# ---------------------------------------------------------------------------
# nokori list --json
# ---------------------------------------------------------------------------


class TestListJson:
    def test_json_output_empty(self, cfg, capsys):
        db = open_db(cfg.db_path)
        db.close()
        args = argparse.Namespace(all=False, project=None, json=True, global_eligible=False)
        rc = run_list(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data == []

    def test_json_output_with_rules(self, cfg, capsys):
        db = open_db(cfg.db_path)
        _insert_rule(db, "abc123", status="active", severity="reminder",
                     trigger="When writing tests use pytest", project_id="proj1")
        _insert_rule(db, "def456", status="trusted", severity="high_risk",
                     trigger="Always validate inputs", project_id=None)
        db.close()

        args = argparse.Namespace(all=False, project=None, json=True, global_eligible=False)
        rc = run_list(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 2
        # Verify structure of each rule object
        for item in data:
            assert set(item.keys()) == {
                "short_id", "status", "trigger", "severity", "project_id", "created_at"
            }
        # Check specific values
        ids = {item["short_id"] for item in data}
        assert "abc123" in ids
        assert "def456" in ids

    def test_json_trigger_truncated_to_100(self, cfg, capsys):
        db = open_db(cfg.db_path)
        long_trigger = "x" * 200
        _insert_rule(db, "trunc1", trigger=long_trigger)
        db.close()

        args = argparse.Namespace(all=False, project=None, json=True, global_eligible=False)
        rc = run_list(args, cfg)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert len(data[0]["trigger"]) == 100

    def test_json_respects_all_flag(self, cfg, capsys):
        db = open_db(cfg.db_path)
        _insert_rule(db, "act1", status="active")
        _insert_rule(db, "arch1", status="archived")
        db.close()

        # Without --all: archived excluded
        args = argparse.Namespace(all=False, project=None, json=True, global_eligible=False)
        run_list(args, cfg)
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert data[0]["short_id"] == "act1"

        # With --all: both visible
        args = argparse.Namespace(all=True, project=None, json=True, global_eligible=False)
        run_list(args, cfg)
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 2

    def test_json_respects_project_filter(self, cfg, capsys):
        db = open_db(cfg.db_path)
        _insert_rule(db, "p1r", status="active", project_id="proj_a")
        _insert_rule(db, "p2r", status="active", project_id="proj_b")
        db.close()

        args = argparse.Namespace(all=False, project="proj_a", json=True, global_eligible=False)
        run_list(args, cfg)
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert data[0]["short_id"] == "p1r"


# ---------------------------------------------------------------------------
# nokori report --metrics
# ---------------------------------------------------------------------------


class TestReportMetrics:
    def test_metrics_empty_db(self, cfg, capsys):
        db = open_db(cfg.db_path)
        db.close()

        args = argparse.Namespace(since=None, session=None, json=False, metrics=True)
        rc = run_report(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Cold-Path Quality Metrics ===" in out
        assert "Extraction: 0 done, 0 pending, 0 failed" in out
        assert "Gate: 0 blocks in last 7 days" in out

    def test_metrics_with_data(self, cfg, capsys):
        db = open_db(cfg.db_path)
        now = now_iso()

        # Insert extraction state rows
        db.conn.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, extracted_at, status) "
            "VALUES (?, 1.0, ?, 'done')", ("/a.jsonl", now))
        db.conn.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, extracted_at, status) "
            "VALUES (?, 1.0, ?, 'done')", ("/b.jsonl", now))
        db.conn.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, extracted_at, status) "
            "VALUES (?, 1.0, ?, 'pending')", ("/c.jsonl", now))

        # Insert rules
        rule_id_1 = _insert_rule(db, "r1", status="active")
        _insert_rule(db, "r2", status="trusted")
        _insert_rule(db, "r3", status="candidate")
        _insert_rule(db, "r4", status="candidate")

        # Insert fire events with posthoc labels (reference real rule_id)
        for label in ("observed_useful", "observed_useful", "irrelevant", "harmful"):
            db.conn.execute(
                "INSERT INTO rule_fire_events (id, rule_id, level, posthoc_label, created_at) "
                "VALUES (?, ?, 'hot', ?, ?)",
                (str(uuid.uuid4()), rule_id_1, label, now),
            )

        # Insert gate blocks
        db.conn.execute(
            "INSERT INTO hook_events (id, source, outcome, created_at) "
            "VALUES (?, 'pre_tool_use', 'blocked', ?)",
            (str(uuid.uuid4()), now),
        )
        db.conn.execute(
            "INSERT INTO hook_events (id, source, outcome, created_at) "
            "VALUES (?, 'pre_tool_use', 'blocked', ?)",
            (str(uuid.uuid4()), now),
        )
        # Non-blocked event (should not count)
        db.conn.execute(
            "INSERT INTO hook_events (id, source, outcome, created_at) "
            "VALUES (?, 'pre_tool_use', 'allowed', ?)",
            (str(uuid.uuid4()), now),
        )
        db.conn.commit()
        db.close()

        args = argparse.Namespace(since=None, session=None, json=False, metrics=True)
        rc = run_report(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Extraction: 2 done, 1 pending, 0 failed" in out
        assert "2 candidate" in out
        assert "1 active" in out
        assert "1 trusted" in out
        assert "2 useful" in out
        assert "1 irrelevant" in out
        assert "1 harmful" in out
        assert "Gate: 2 blocks in last 7 days" in out

    def test_metrics_json_output(self, cfg, capsys):
        db = open_db(cfg.db_path)
        now = now_iso()
        _insert_rule(db, "jr1", status="active")
        db.conn.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, extracted_at, status) "
            "VALUES (?, 1.0, ?, 'done')", ("/x.jsonl", now))
        db.conn.commit()
        db.close()

        args = argparse.Namespace(since=None, session=None, json=True, metrics=True)
        rc = run_report(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "extraction" in data
        assert "rules" in data
        assert "posthoc_30d" in data
        assert "gate_blocks_7d" in data
        assert data["extraction"]["done"] == 1
        assert data["rules"]["active"] == 1
