"""Tests for nokori report and nokori stream CLI commands."""
from __future__ import annotations

import argparse
import json

import pytest

from nokori.commands.report import run as run_report
from nokori.commands.stream import run as run_stream
from nokori.config import Config
from nokori.db import open_db
from nokori.events.observability import write_error, write_event


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    return Config.from_env()


@pytest.fixture
def seeded_db(cfg):
    db = open_db(cfg.db_path)
    write_event(db, source="session_start", session_id="s1", outcome="ok", details={"rule_count": 5})
    write_event(db, source="user_prompt_submit", session_id="s1", outcome="injected", details={"hot_count": 2})
    write_event(db, source="cold_pipeline", outcome="active", details={"trigger_preview": "test"})
    write_error(db, source="cold_pipeline", role="extractor", error_type="timeout", message="timed out", model_id="test-model")
    db.close()


class TestReport:
    def test_report_markdown(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(since=None, session=None, json=False)
        rc = run_report(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "# Nokori Report" in out
        assert "Events:" in out
        assert "Errors:" in out

    def test_report_json(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(since=None, session=None, json=True)
        rc = run_report(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["usage"]["total_events"] == 3
        assert data["usage"]["total_errors"] == 1
        assert data["usage"]["sessions"] == 1

    def test_report_session_filter(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(since=None, session="s1", json=True)
        rc = run_report(args, cfg)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["usage"]["total_events"] == 2

    def test_report_empty_db(self, cfg, capsys):
        db = open_db(cfg.db_path)
        db.close()
        args = argparse.Namespace(since=None, session=None, json=True)
        rc = run_report(args, cfg)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["usage"]["total_events"] == 0


class TestStream:
    def test_stream_dump_mode(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(
            since=None, session=None, type=None,
            verbose=False, limit=100, follow=False,
        )
        rc = run_stream(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 3

    def test_stream_verbose_mode(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(
            since=None, session=None, type=None,
            verbose=True, limit=100, follow=False,
        )
        rc = run_stream(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        for line in lines:
            parsed = json.loads(line)
            assert "source" in parsed
            assert "created_at" in parsed

    def test_stream_type_filter(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(
            since=None, session=None, type="cold_pipeline",
            verbose=False, limit=100, follow=False,
        )
        rc = run_stream(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 1
        assert "cold_pipeline" in lines[0]

    def test_stream_session_filter(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(
            since=None, session="s1", type=None,
            verbose=False, limit=100, follow=False,
        )
        rc = run_stream(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 2

    def test_stream_limit(self, cfg, seeded_db, capsys):
        args = argparse.Namespace(
            since=None, session=None, type=None,
            verbose=False, limit=1, follow=False,
        )
        rc = run_stream(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 1

    def test_stream_empty_db(self, cfg, capsys):
        db = open_db(cfg.db_path)
        db.close()
        args = argparse.Namespace(
            since=None, session=None, type=None,
            verbose=False, limit=100, follow=False,
        )
        rc = run_stream(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == ""
