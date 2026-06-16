"""Tests for nokori.lifecycle.hot_cache module."""
from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nokori.db import open_db
from nokori.lifecycle.hot_cache import (
    _recent_trusted_rules_summary,
    find_previous_transcript,
    maybe_inject,
)


def _make_db(tmp_path: Path):
    db = open_db(tmp_path / "rules.db")
    return db


def _insert_rule(db, *, rule_id=None, status="trusted", trigger="trigger", action="action"):
    rule_id = rule_id or uuid.uuid4().hex
    short_id = rule_id[:6]
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "status, severity, project_scope, created_at, updated_at) "
            "VALUES (?, ?, 6, 1, 'v1', '1.0.0', ?, ?, ?, 'reminder', 'global', ?, ?)",
            (rule_id, short_id, trigger, action, status, now, now),
        )
    return rule_id


def _insert_fire_event(db, rule_id: str, *, created_at: str | None = None):
    event_id = uuid.uuid4().hex
    if created_at is None:
        created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, created_at) VALUES (?, ?, ?)",
            (event_id, rule_id, created_at),
        )
    return event_id


def _iso_days_ago(days: int) -> str:
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# _recent_trusted_rules_summary
# ---------------------------------------------------------------------------


class TestRecentTrustedRulesSummary:
    def test_no_fire_events_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            result = _recent_trusted_rules_summary(db)
            assert result is None
        finally:
            db.close()

    def test_fire_events_within_window(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule_id = _insert_rule(db, trigger="user asks about X", action="respond with Y")
            _insert_fire_event(db, rule_id, created_at=_iso_days_ago(2))
            result = _recent_trusted_rules_summary(db)
            assert result is not None
            assert "user asks about X" in result
            assert "respond with Y" in result
            assert "[Nokori hot-cache]" in result
        finally:
            db.close()

    def test_fire_events_older_than_window_excluded(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule_id = _insert_rule(db, trigger="old trigger", action="old action")
            _insert_fire_event(db, rule_id, created_at=_iso_days_ago(10))
            result = _recent_trusted_rules_summary(db)
            assert result is None
        finally:
            db.close()

    def test_none_trigger_shows_only_action(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule_id = _insert_rule(db, trigger="", action="just do this")
            _insert_fire_event(db, rule_id, created_at=_iso_days_ago(1))
            result = _recent_trusted_rules_summary(db)
            assert result is not None
            assert "just do this" in result
            # When trigger is empty, format is just action (no "->")
            assert " -> " not in result
        finally:
            db.close()

    def test_budget_enforcement_truncates(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            # Create many rules with long text to exceed budget
            for i in range(5):
                rule_id = _insert_rule(
                    db,
                    trigger=f"trigger number {i} " + "x" * 60,
                    action=f"action number {i} " + "y" * 60,
                )
                _insert_fire_event(db, rule_id, created_at=_iso_days_ago(1))
            result = _recent_trusted_rules_summary(db)
            assert result is not None
            assert len(result) <= 500
        finally:
            db.close()

    def test_only_active_trusted_included(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            # candidate rule — should not appear
            candidate_id = _insert_rule(db, status="candidate", trigger="candidate trigger", action="candidate action")
            _insert_fire_event(db, candidate_id, created_at=_iso_days_ago(1))

            # suppressed rule — should not appear
            suppressed_id = _insert_rule(db, status="suppressed", trigger="suppressed trigger", action="suppressed action")
            _insert_fire_event(db, suppressed_id, created_at=_iso_days_ago(1))

            # active rule — should appear
            active_id = _insert_rule(db, status="active", trigger="active trigger", action="active action")
            _insert_fire_event(db, active_id, created_at=_iso_days_ago(1))

            result = _recent_trusted_rules_summary(db)
            assert result is not None
            assert "active trigger" in result
            assert "candidate trigger" not in result
            assert "suppressed trigger" not in result
        finally:
            db.close()


# ---------------------------------------------------------------------------
# find_previous_transcript
# ---------------------------------------------------------------------------


def _make_transcript(directory: Path, name: str, content: str = "", mtime_offset: float = 0.0) -> Path:
    p = directory / name
    p.write_text(content)
    base_time = time.time() + mtime_offset
    os.utime(p, (base_time, base_time))
    return p


class TestFindPreviousTranscript:
    def test_no_glob_matches_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        current = tmp_path / "current.jsonl"
        current.write_text("")
        # No other jsonl files exist
        result = find_previous_transcript(current)
        assert result is None

    def test_returns_previous_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        # Create files with distinct mtimes
        prev = _make_transcript(tmp_path, "prev.jsonl", '{"type":"user","message":"hello"}\n', mtime_offset=-2)
        current = _make_transcript(tmp_path, "current.jsonl", '{"type":"user","message":"now"}\n', mtime_offset=0)
        result = find_previous_transcript(current)
        assert result is not None
        assert result == prev.resolve()

    def test_picks_newest_previous(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        _make_transcript(tmp_path, "oldest.jsonl", "", mtime_offset=-10)
        newer = _make_transcript(tmp_path, "newer.jsonl", "", mtime_offset=-2)
        current = _make_transcript(tmp_path, "current.jsonl", "", mtime_offset=0)
        result = find_previous_transcript(current)
        assert result == newer.resolve()


# ---------------------------------------------------------------------------
# maybe_inject
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, *, hot_cache_enabled: bool = True):
    from nokori.config import Config
    return Config(
        data_dir=tmp_path,
        max_injection_chars=1500,
        gate_enabled=False,
        gate_ttl_seconds=600,
        gate_matcher="nokori:",
        extract_mode="manual",
        extract_defer_when_active=False,
        extract_fork_cache=False,
        llm_base_url=None,
        llm_model=None,
        llm_api_key=None,
        embed_enabled=False,
        embed_base_url=None,
        embed_model=None,
        embed_api_key=None,
        embed_dimensions=0,
        embed_chunk_size=4000,
        embed_chunk_count=2,
        embed_chunk_size_configured=False,
        embed_chunk_count_configured=False,
        embed_hook_timeout_seconds=2,
        embed_server_idle_seconds=3600,
        embed_server_auto_start=True,
        hot_cache_enabled=hot_cache_enabled,
        session_idle_seconds=1800,
        promotion_enabled=True,
        strict=False,
        disabled=False,
        dismiss_phrase="dismiss",
        role_models={},
        role_max_tokens={},
        role_timeouts={},
        log_level="warn",
    )


class TestMaybeInject:
    def test_returns_none_no_prev_no_rules(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        db = _make_db(tmp_path)
        cfg = _make_config(tmp_path)
        try:
            # Single transcript, no previous, no rules
            current = _make_transcript(tmp_path, "current.jsonl", '{"type":"user","message":"hi"}\n')
            payload = {"transcript_path": str(current)}
            result = maybe_inject(payload, cfg, db)
            assert result is None
        finally:
            db.close()

    def test_returns_only_transcript_section(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        db = _make_db(tmp_path)
        cfg = _make_config(tmp_path)
        try:
            # Previous transcript with user messages
            prev_content = '{"type":"user","message":"previous question"}\n'
            _make_transcript(tmp_path, "prev.jsonl", prev_content, mtime_offset=-2)
            current = _make_transcript(tmp_path, "current.jsonl", '{"type":"user","message":"now"}\n', mtime_offset=0)
            payload = {"transcript_path": str(current)}
            result = maybe_inject(payload, cfg, db)
            assert result is not None
            assert "previous question" in result
            assert "last messages from the previous session" in result
            # No trusted rules section
            assert "recently active trusted rules" not in result
        finally:
            db.close()

    def test_returns_only_trusted_rules_section(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        db = _make_db(tmp_path)
        cfg = _make_config(tmp_path)
        try:
            # No previous transcript (single file)
            current = _make_transcript(tmp_path, "current.jsonl", '{"type":"user","message":"now"}\n')
            # Add a trusted rule with recent fire event
            rule_id = _insert_rule(db, trigger="pattern X", action="do Y")
            _insert_fire_event(db, rule_id, created_at=_iso_days_ago(1))
            payload = {"transcript_path": str(current)}
            result = maybe_inject(payload, cfg, db)
            assert result is not None
            assert "recently active trusted rules" in result
            assert "pattern X" in result
            assert "last messages from the previous session" not in result
        finally:
            db.close()

    def test_returns_combined_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_TRANSCRIPT_EXTRA_ROOTS", str(tmp_path))
        db = _make_db(tmp_path)
        cfg = _make_config(tmp_path)
        try:
            # Previous transcript
            prev_content = '{"type":"user","message":"previous msg"}\n'
            _make_transcript(tmp_path, "prev.jsonl", prev_content, mtime_offset=-2)
            current = _make_transcript(tmp_path, "current.jsonl", '{"type":"user","message":"now"}\n', mtime_offset=0)
            # Trusted rule with fire event
            rule_id = _insert_rule(db, trigger="trigger Z", action="action Z")
            _insert_fire_event(db, rule_id, created_at=_iso_days_ago(1))
            payload = {"transcript_path": str(current)}
            result = maybe_inject(payload, cfg, db)
            assert result is not None
            assert "last messages from the previous session" in result
            assert "recently active trusted rules" in result
            assert "previous msg" in result
            assert "trigger Z" in result
            # Sections separated by double newline
            assert "\n\n" in result
        finally:
            db.close()
