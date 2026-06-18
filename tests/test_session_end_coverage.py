"""Coverage tests for hooks/session_end.py.

Covers: posthoc enqueue, extract job creation, transcript window population,
fork extract attempt, observability event writing, error paths.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.events.fire import create_fire_event
from nokori.gate.marker import prompt_hash
from nokori.hooks.session_end import (
    _EXTRACT_SAFE_PREFIXES,
    _EXTRACT_SAFE_VARS,
    _enqueue_extract_job_from_path,
    _extract_session_turns,
    _populate_transcript_windows,
    _spawn_async_extract,
    handle,
)
from nokori.models import Rule
from nokori.posthoc.jobs import enqueue_posthoc_for_session
from nokori.utils.host import Host
from nokori.utils.prompt_text import normalize_prompt_for_hash
from nokori.utils.time import now_iso


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EXTRACT_MODE", "manual")
    cfg = Config.from_env()
    cfg.ensure_dirs()
    yield cfg, tmp_path


class TestSessionEndHandle:
    def test_disabled_returns_immediately(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_DISABLED", "1")
        cfg = Config.from_env()
        result = handle({"session_id": "test-sess"}, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    def test_basic_session_end_no_transcript(self, session_env):
        cfg, tmp_path = session_env
        payload = {"session_id": "sess-end-1", "cwd": str(tmp_path)}
        with patch("nokori.hooks.session_end.resolve_transcript_path", return_value=None):
            result = handle(payload, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    def test_session_end_with_transcript(self, session_env):
        cfg, tmp_path = session_env
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user","content":"hello"}\n')

        payload = {"session_id": "sess-end-2", "cwd": str(tmp_path)}
        with patch("nokori.hooks.session_end.resolve_transcript_path", return_value=transcript):
            result = handle(payload, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    def test_session_end_posthoc_db_open_fails(self, session_env):
        from nokori.errors import DbError

        cfg, tmp_path = session_env
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user","content":"test"}\n')
        payload = {"session_id": "sess-end-3", "cwd": str(tmp_path)}
        with (
            patch("nokori.hooks.context.open_db", side_effect=DbError("db locked")),
            patch("nokori.hooks.session_end.resolve_transcript_path", return_value=transcript) as mock_rtp,
        ):
            result = handle(payload, cfg, host=Host.CLAUDE)
            assert result == {"continue": True}
            mock_rtp.assert_called_once()


class TestExtractJobEnqueue:
    def test_no_transcript_path_returns_none(self, session_env):
        cfg, _ = session_env
        assert _enqueue_extract_job_from_path(None, {}, cfg) is None

    def test_nonexistent_path_returns_none(self, session_env):
        cfg, tmp_path = session_env
        assert _enqueue_extract_job_from_path(tmp_path / "ghost.jsonl", {}, cfg) is None

    def test_valid_transcript_creates_job(self, session_env):
        cfg, tmp_path = session_env
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user"}\n')

        result = _enqueue_extract_job_from_path(
            transcript,
            {"cwd": str(tmp_path)},
            cfg,
        )
        assert result is not None
        assert result.exists()
        assert list(cfg.jobs_dir.glob("extract-*.json"))


class TestExtractSessionTurns:
    def test_empty_payload_returns_empty(self):
        assert _extract_session_turns({}) == []

    def test_messages_list_parsed(self):
        payload = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        }
        turns = _extract_session_turns(payload)
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

    def test_conversation_key_also_works(self):
        payload = {
            "conversation": [
                {"role": "user", "content": "test", "tool_name": "Bash", "tool_input": "ls"},
            ]
        }
        turns = _extract_session_turns(payload)
        assert len(turns) == 1
        assert turns[0]["tool_name"] == "Bash"

    def test_non_dict_messages_skipped(self):
        payload = {"messages": ["string_entry", {"role": "user", "content": "ok"}]}
        turns = _extract_session_turns(payload)
        assert len(turns) == 1

    def test_non_list_messages_returns_empty(self):
        payload = {"messages": "not a list"}
        assert _extract_session_turns(payload) == []


class TestExtractSubprocessEnv:
    """Extract subprocesses must inherit proxy/cert/anthropic env so the claude
    CLI can reach its API in corporate networks. Guards against silent fork
    failures from an over-restrictive env whitelist."""

    def test_safe_vars_include_proxy_and_cert(self):
        for var in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "NODE_EXTRA_CA_CERTS",
        ):
            assert var in _EXTRACT_SAFE_VARS, f"{var} missing from extract env whitelist"

    def test_safe_prefixes_include_anthropic_and_claude(self):
        assert "NOKORI_" in _EXTRACT_SAFE_PREFIXES
        assert "ANTHROPIC_" in _EXTRACT_SAFE_PREFIXES
        assert "CLAUDE_" in _EXTRACT_SAFE_PREFIXES

    def test_spawn_async_extract_passes_through_anthropic_and_proxy(self, session_env):
        cfg, _ = session_env
        captured: dict = {}

        class _FakePopen:
            def __init__(self, cmd, env=None, **kwargs):
                captured["env"] = env

        with (
            patch("subprocess.Popen", _FakePopen),
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_API_KEY": "sk-test",
                    "ANTHROPIC_BASE_URL": "http://custom:8080",
                    "HTTPS_PROXY": "http://proxy:3128",
                    "SSL_CERT_FILE": "/etc/ssl/corp.pem",
                    "RANDOM_USER_VAR": "should-not-leak",
                },
                clear=False,
            ),
        ):
            _spawn_async_extract(cfg)

        env = captured["env"]
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert env["ANTHROPIC_BASE_URL"] == "http://custom:8080"
        assert env["HTTPS_PROXY"] == "http://proxy:3128"
        assert env["SSL_CERT_FILE"] == "/etc/ssl/corp.pem"
        assert env["NOKORI_DATA_DIR"] == str(cfg.data_dir)
        # Unlisted vars must NOT leak into the subprocess.
        assert "RANDOM_USER_VAR" not in env
        assert env.get("NOKORI_EXTRACTING") is None


# ---------------------------------------------------------------------------
# _populate_transcript_windows: turn_index=None must locate via prompt_hash
# (task 06-18-fix-posthoc-window-active-fire-loop, AC2)
# ---------------------------------------------------------------------------


def _insert_rule_for_window(db, *, status="active") -> Rule:
    rule_id = str(uuid.uuid4())
    short_id = rule_id[:6]
    now = now_iso()
    concepts = json.dumps(["concept_a"])
    groups = json.dumps(["group_1"])
    excluded = json.dumps(["excluded_ctx"])
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "status, severity, "
            "trigger_canonical, concepts, required_concept_groups, excluded_contexts, "
            "trigger_variants, action_instruction, "
            "domain_tags, tool_tags, path_patterns, "
            "source_origin, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id, short_id, 1, 1, "pipeline_v1", "policy_v1",
                status, "reminder",
                "trigger text", concepts, groups, excluded,
                json.dumps(["variant_a"]), "do the action",
                json.dumps(["domain_web"]), json.dumps(["tool_git"]),
                json.dumps(["src/**"]),
                "transcript_extraction", "project", now, now,
            ),
        )
    return Rule(
        id=rule_id, short_id=short_id, schema_version=1, rule_version=1,
        created_by_pipeline_version="pipeline_v1",
        runtime_policy_version="policy_v1",
        last_rewritten_by_role=None, status=status, severity="reminder",
        trigger_canonical="trigger text", concepts=concepts,
        required_concept_groups=groups, excluded_contexts=excluded,
        near_miss_examples=[], trigger_variants=["variant_a"],
        action_instruction="do the action",
        domain_tags=["domain_web"], tool_tags=["tool_git"],
        path_patterns=["src/**"],
        quality_score=0.0, evidence_support_score=0.0,
        specificity_score=0.0, retrieval_readiness_score=0.0,
        observed_usefulness_score=0.0, plausible_usefulness_score=0.0,
        false_positive_score=0.0, harmful_score=0.0,
        source_origin="transcript_extraction", activation_origin=None,
        first_observed_useful_at=None, trusted_at=None, suppressed_at=None,
        project_scope="project", project_id=None,
        archived_reason=None, replacement_id=None,
        created_at=now, updated_at=now,
    )


class TestPopulateTranscriptWindowsPromptHashFallback:
    """AC2: when fire event turn_index is None (Claude Code / Cursor
    UserPromptSubmit payloads), redacted_window_json must still be populated
    by locating the injection turn via prompt_hash against session_turns."""

    def test_turn_index_none_populates_window_via_prompt_hash(self, tmp_path):
        db = open_db(tmp_path / "rules.db")
        try:
            rule = _insert_rule_for_window(db)
            session_id = "sess-ph-1"
            user_prompt = "please explain the database migration approach"
            normalized = normalize_prompt_for_hash(user_prompt)
            # Hash caliber mirrors session_end.py / prompt_inject.py.
            ph = prompt_hash(normalized or user_prompt)

            create_fire_event(
                db, rule, session_id, ph, "hot", {"score": 0.9},
                turn_index=None,  # UserPromptSubmit has no turn_index
                bounded_window_ref=f"session:{session_id}:prompt:{ph}",
            )
            enqueue_posthoc_for_session(db, session_id)

            # turn_index values are non-contiguous strings ("10", "11") to
            # ensure the hash fallback coerces to int and writes back to the
            # turn (compute_event_window matches by turn_index field) — a
            # regression that keeps the string or uses the list position would
            # fail to anchor the window.
            session_turns = [
                {"role": "user", "content": user_prompt, "turn_index": "10"},
                {
                    "role": "assistant",
                    "content": "I'll explain the migration approach.",
                    "turn_index": "11",
                },
            ]
            _populate_transcript_windows(db, session_id, session_turns)

            row = db.fetchone(
                "SELECT redacted_window_json FROM posthoc_jobs "
                "WHERE fire_event_id = (SELECT id FROM rule_fire_events WHERE session_id = ?)",
                (session_id,),
            )
            assert row is not None
            assert row["redacted_window_json"] is not None
            window = row["redacted_window_json"]
            # Assert a phrase unique to the assistant turn so the test
            # verifies the window includes the full bounded context, not just
            # the injection user turn.
            assert "I'll explain the migration approach." in window
        finally:
            db.close()

    def test_turn_index_none_skipped_when_prompt_hash_not_in_turns(self, tmp_path):
        """When session_turns doesn't contain the injection prompt (e.g. session_end
        payload lacks the full transcript), redacted_window_json stays NULL —
        the posthoc background worker re-derives from the transcript file."""
        db = open_db(tmp_path / "rules.db")
        try:
            rule = _insert_rule_for_window(db)
            session_id = "sess-ph-2"
            # prompt_hash that won't match any turn in session_turns
            ph = prompt_hash("a prompt that is not in session_turns")

            create_fire_event(
                db, rule, session_id, ph, "hot", {"score": 0.9},
                turn_index=None,
                bounded_window_ref=f"session:{session_id}:prompt:{ph}",
            )
            enqueue_posthoc_for_session(db, session_id)

            session_turns = [
                {"role": "user", "content": "totally different content", "turn_index": 0},
            ]
            _populate_transcript_windows(db, session_id, session_turns)

            row = db.fetchone(
                "SELECT redacted_window_json FROM posthoc_jobs "
                "WHERE fire_event_id = (SELECT id FROM rule_fire_events WHERE session_id = ?)",
                (session_id,),
            )
            assert row is not None
            # Stays NULL — posthoc worker will re-derive from transcript file.
            assert row["redacted_window_json"] is None
        finally:
            db.close()

    def test_turn_index_none_with_duplicate_prompt_hash_anchors_first_match(self, tmp_path):
        """Document the prompt_hash fallback's anchor choice with duplicate prompts.

        When turn_index is None and the same prompt_hash appears twice in a
        session, the fallback anchors on the first matching turn. This is the
        intended (conservative) behavior — duplicate prompts in one session are
        rare, and first-match keeps the window anchored to the original
        injection. The direct turn_index path (tested separately) disambiguates
        when turn_index is present.
        """
        db = open_db(tmp_path / "rules.db")
        try:
            rule = _insert_rule_for_window(db)
            session_id = "sess-ph-duplicate-none"
            prompt = "repeated triggering prompt"
            ph = prompt_hash(normalize_prompt_for_hash(prompt) or prompt)
            create_fire_event(
                db, rule, session_id, ph, "hot", {"score": 0.9},
                turn_index=None,
                bounded_window_ref=f"session:{session_id}:prompt:{ph}",
            )
            enqueue_posthoc_for_session(db, session_id)

            session_turns = [
                {"role": "user", "content": prompt, "turn_index": 0},
                {"role": "assistant", "content": "response to first duplicate", "turn_index": 1},
                {"role": "user", "content": prompt, "turn_index": 2},
                {"role": "assistant", "content": "response to second duplicate", "turn_index": 3},
            ]
            _populate_transcript_windows(db, session_id, session_turns)

            row = db.fetchone(
                "SELECT redacted_window_json FROM posthoc_jobs "
                "WHERE fire_event_id = (SELECT id FROM rule_fire_events WHERE session_id = ?)",
                (session_id,),
            )
            assert row is not None
            assert row["redacted_window_json"] is not None
            # Fallback chooses the first matching hash.
            assert "response to first duplicate" in row["redacted_window_json"]
        finally:
            db.close()

    def test_turn_index_present_uses_turn_index_directly(self, tmp_path):
        """Regression: when turn_index is present and matches prompt_hash, use it directly.

        Uses a repeated prompt so direct turn_index lookup (2nd occurrence)
        is distinguishable from hash fallback (which would anchor on the 1st).
        """
        db = open_db(tmp_path / "rules.db")
        try:
            rule = _insert_rule_for_window(db)
            session_id = "sess-ph-3"
            prompt = "repeated triggering prompt"
            ph = prompt_hash(normalize_prompt_for_hash(prompt) or prompt)
            create_fire_event(
                db, rule, session_id, ph, "hot", {"score": 0.9},
                turn_index=22,
                bounded_window_ref=f"session:{session_id}:prompt:{ph}",
            )
            enqueue_posthoc_for_session(db, session_id)

            # Non-contiguous turn_index values (20-23) so a regression that
            # indexes session_turns by position (instead of by turn_index field)
            # would fail to anchor on the 2nd occurrence.
            session_turns = [
                {"role": "user", "content": prompt, "turn_index": 20},
                {"role": "assistant", "content": "response to first duplicate", "turn_index": 21},
                {"role": "user", "content": prompt, "turn_index": 22},
                {"role": "assistant", "content": "response to second duplicate", "turn_index": 23},
            ]
            _populate_transcript_windows(db, session_id, session_turns)

            row = db.fetchone(
                "SELECT redacted_window_json FROM posthoc_jobs "
                "WHERE fire_event_id = (SELECT id FROM rule_fire_events WHERE session_id = ?)",
                (session_id,),
            )
            assert row is not None
            assert row["redacted_window_json"] is not None
            # Direct turn_index=22 field lookup anchors on the 2nd occurrence —
            # its response must be present, the 1st occurrence's must not.
            assert "response to second duplicate" in row["redacted_window_json"]
            assert "response to first duplicate" not in row["redacted_window_json"]
        finally:
            db.close()

    def test_turn_index_without_hash_rejects_non_user_turn(self, tmp_path):
        """When prompt_hash is absent, turn_index must still point at a user turn."""
        db = open_db(tmp_path / "rules.db")
        try:
            rule = _insert_rule_for_window(db)
            session_id = "sess-ph-no-hash"
            create_fire_event(
                db, rule, session_id, "", "hot", {"score": 0.9},
                turn_index=1,  # points at assistant, not a user injection turn
                bounded_window_ref=f"session:{session_id}:prompt:",
            )
            enqueue_posthoc_for_session(db, session_id)

            session_turns = [
                {"role": "user", "content": "first user prompt", "turn_index": 0},
                {"role": "assistant", "content": "assistant should not anchor", "turn_index": 1},
            ]
            _populate_transcript_windows(db, session_id, session_turns)

            row = db.fetchone(
                "SELECT redacted_window_json FROM posthoc_jobs "
                "WHERE fire_event_id = (SELECT id FROM rule_fire_events WHERE session_id = ?)",
                (session_id,),
            )
            assert row is not None
            # Non-user turn_index without hash must not anchor a window.
            assert row["redacted_window_json"] is None
        finally:
            db.close()

    def test_turn_index_hash_mismatch_falls_back_to_hash_lookup(self, tmp_path):
        """turn_index present but its turn's hash != fire event hash → hash lookup."""
        db = open_db(tmp_path / "rules.db")
        try:
            rule = _insert_rule_for_window(db)
            session_id = "sess-ph-mismatch"
            actual_prompt = "the real triggering prompt"
            ph = prompt_hash(normalize_prompt_for_hash(actual_prompt) or actual_prompt)
            # turn_index points at turn 0 (a different user prompt), but the
            # fire event's hash corresponds to turn 2 — must not anchor on 0.
            create_fire_event(
                db, rule, session_id, ph, "hot", {"score": 0.9},
                turn_index=0,
                bounded_window_ref=f"session:{session_id}:prompt:{ph}",
            )
            enqueue_posthoc_for_session(db, session_id)

            session_turns = [
                {"role": "user", "content": "a different first turn", "turn_index": 0},
                {"role": "assistant", "content": "response one", "turn_index": 1},
                {"role": "user", "content": actual_prompt, "turn_index": 2},
            ]
            _populate_transcript_windows(db, session_id, session_turns)

            row = db.fetchone(
                "SELECT redacted_window_json FROM posthoc_jobs "
                "WHERE fire_event_id = (SELECT id FROM rule_fire_events WHERE session_id = ?)",
                (session_id,),
            )
            assert row is not None
            assert row["redacted_window_json"] is not None
            # Window anchored at the hash-matched turn (2), not turn_index 0.
            assert actual_prompt in row["redacted_window_json"]
            assert "a different first turn" not in row["redacted_window_json"]
        finally:
            db.close()
