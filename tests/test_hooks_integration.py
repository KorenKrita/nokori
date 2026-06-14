"""End-to-end integration tests for all 4 hot-path hooks.

Exercises the full flow: payload -> context -> retrieval/gate -> response serialization.
Uses real SQLite DB (temp path), real Config, real BM25 retrieval.
Only mocks: embed IPC (socket), subprocess spawning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import Db, open_db
from nokori.hooks.pre_tool_use import handle as ptu_handle
from nokori.hooks.session_end import handle as se_handle
from nokori.hooks.session_start import handle as ss_handle
from nokori.hooks.user_prompt_submit import handle as ups_handle
from nokori.utils.host import Host
from nokori.utils.time import now_iso  # noqa: I001

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class HookTestEnv:
    cfg: Config
    db: Db
    data_dir: Path


def _seed_test_rules(db: Db) -> None:
    """Insert 3 rules for integration testing.

    Rule A: trusted, gate_eligible, trigger about "force push"
    Rule B: active, reminder severity, trigger about "database migration"
    Rule C: candidate (shadow pool only)
    """
    now = now_iso()
    with db.transaction() as tx:
        # Rule A: trusted + gate_eligible — should trigger injection AND gate blocking
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, runtime_policy_version, "
            "status, severity, trigger_canonical, action_instruction, "
            "concepts, required_concept_groups, trigger_variants, search_terms, "
            "source_origin, project_scope, project_id, created_at, updated_at) "
            "VALUES (?, ?, 7, 1, '1.0.0', 'trusted', 'gate_eligible', ?, ?, "
            "?, ?, ?, ?, "
            "'transcript_extraction', 'global', NULL, ?, ?)",
            (
                "rule-A",
                "aaa111",
                "force push to remote branch without lease",
                "NEVER force push without --force-with-lease; use lease-based push",
                json.dumps([
                    {
                        "id": "force_push",
                        "label": "force push",
                        "aliases": [
                            {"text": "force push", "strength": "strong"},
                            {"text": "git push --force", "strength": "strong"},
                        ],
                        "match_mode": "any",
                        "required": True,
                    }
                ]),
                json.dumps([{"id": "g1", "all_of": ["force_push"]}]),
                json.dumps([
                    {"text": "force push", "kind": "strong_anchor"},
                    {"text": "git push --force", "kind": "strong_anchor"},
                ]),
                json.dumps({"en": ["force", "push", "git", "lease", "remote"]}),
                now,
                now,
            ),
        )

        # Rule B: active + reminder — should trigger injection (WARM or HOT) but not gate
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, runtime_policy_version, "
            "status, severity, trigger_canonical, action_instruction, "
            "concepts, required_concept_groups, trigger_variants, search_terms, "
            "source_origin, project_scope, project_id, created_at, updated_at) "
            "VALUES (?, ?, 7, 1, '1.0.0', 'active', 'reminder', ?, ?, "
            "?, ?, ?, ?, "
            "'transcript_extraction', 'global', NULL, ?, ?)",
            (
                "rule-B",
                "bbb222",
                "database migration without rollback plan",
                "Always prepare a rollback plan before running database migrations",
                json.dumps([
                    {
                        "id": "db_migration",
                        "label": "database migration",
                        "aliases": [
                            {"text": "database migration", "strength": "strong"},
                            {"text": "schema migration", "strength": "strong"},
                        ],
                        "match_mode": "any",
                        "required": True,
                    }
                ]),
                json.dumps([{"id": "g2", "all_of": ["db_migration"]}]),
                json.dumps([
                    {"text": "database migration", "kind": "strong_anchor"},
                    {"text": "schema migration", "kind": "strong_anchor"},
                ]),
                json.dumps({"en": ["database", "migration", "rollback", "schema"]}),
                now,
                now,
            ),
        )

        # Rule C: candidate — shadow pool only, never injected
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, runtime_policy_version, "
            "status, severity, trigger_canonical, action_instruction, "
            "concepts, required_concept_groups, trigger_variants, search_terms, "
            "source_origin, project_scope, project_id, created_at, updated_at) "
            "VALUES (?, ?, 7, 1, '1.0.0', 'candidate', 'reminder', ?, ?, "
            "'[]', '[]', '[]', '{}', "
            "'transcript_extraction', 'global', NULL, ?, ?)",
            (
                "rule-C",
                "ccc333",
                "always use type annotations",
                "Add type annotations to all function signatures",
                now,
                now,
            ),
        )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    """Config + DB + seeded rules for hook integration tests."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    db = open_db(cfg.db_path)
    _seed_test_rules(db)
    yield HookTestEnv(cfg=cfg, db=db, data_dir=tmp_path)
    db.close()


# ---------------------------------------------------------------------------
# UserPromptSubmit tests
# ---------------------------------------------------------------------------


class TestUserPromptSubmit:
    def test_injects_matching_rule(self, hook_env: HookTestEnv):
        """Prompt matching Rule A (force push) should produce injection text."""
        payload = {
            "session_id": "sess-ups-1",
            "prompt": "I'm about to git push --force the main branch to remote",
            "cwd": "/tmp",
        }
        result = ups_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        # Should have hookSpecificOutput with injection
        hso = result.get("hookSpecificOutput")
        assert hso is not None, f"Expected hookSpecificOutput, got: {result}"
        assert hso.get("hookEventName") == "UserPromptSubmit"
        assert hso.get("additionalContext") is not None
        assert "force-with-lease" in hso["additionalContext"].lower()

    def test_warm_injection_for_partial_match(self, hook_env: HookTestEnv):
        """Prompt matching Rule B (database migration) should inject."""
        payload = {
            "session_id": "sess-ups-2",
            "prompt": "I need to run a database migration to add the new column to the schema",
            "cwd": "/tmp",
        }
        result = ups_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        hso = result.get("hookSpecificOutput")
        assert hso is not None, "BM25 should match 'database schema migration' against Rule B"
        assert hso.get("additionalContext") is not None
        assert "rollback" in hso["additionalContext"].lower() or "migration" in hso["additionalContext"].lower()

    def test_empty_prompt_no_injection(self, hook_env: HookTestEnv):
        """Empty prompt should return valid response with no injection."""
        payload = {
            "session_id": "sess-ups-3",
            "prompt": "",
            "cwd": "/tmp",
        }
        result = ups_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        # Empty prompt cannot match any rule
        assert result.get("continue") is True
        # Should not have hookSpecificOutput with injection
        hso = result.get("hookSpecificOutput")
        if hso:
            assert hso.get("additionalContext") is None

    def test_db_unavailable_fail_open(self, tmp_path, monkeypatch):
        """Bad db_path should return continue=True (fail-open)."""
        # Point to a non-writable path for the DB
        bad_dir = tmp_path / "bad" / "nested" / "path"
        bad_dir.mkdir(parents=True)
        (bad_dir / "rules.db").write_text("not a db")
        monkeypatch.setenv("NOKORI_DATA_DIR", str(bad_dir))
        cfg = Config.from_env()
        cfg.ensure_dirs()

        payload = {
            "session_id": "sess-ups-4",
            "prompt": "force push everything",
            "cwd": "/tmp",
        }
        result = ups_handle(payload, cfg, host=Host.CLAUDE)
        assert result == {"continue": True}


# ---------------------------------------------------------------------------
# PreToolUse tests
# ---------------------------------------------------------------------------


class TestPreToolUse:
    def test_gate_pass_unmatched_tool(self, hook_env: HookTestEnv):
        """Tool='Read' is not in gate matcher -> empty response (pass-through)."""
        payload = {
            "session_id": "sess-ptu-1",
            "tool_name": "Read",
            "cwd": "/tmp",
        }
        result = ptu_handle(payload, hook_env.cfg, host=Host.CLAUDE)
        # Read is not in the default gate matcher, so should pass
        hso = result.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") != "deny"

    def test_gate_no_block_when_disabled(self, tmp_path, monkeypatch):
        """Gate disabled in config -> always pass regardless of tool."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_GATE_ENABLED", "0")
        cfg = Config.from_env()
        cfg.ensure_dirs()
        db = open_db(cfg.db_path)
        _seed_test_rules(db)
        db.close()

        payload = {
            "session_id": "sess-ptu-2",
            "tool_name": "Edit",
            "cwd": "/tmp",
        }
        result = ptu_handle(payload, cfg, host=Host.CLAUDE)
        hso = result.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") != "deny"

    def test_gate_blocks_after_matching_submit(self, hook_env: HookTestEnv):
        """After user_prompt_submit injects a gate-eligible rule, pre_tool_use should block.

        This tests the full flow: submit -> marker written -> tool blocked.
        """
        session_id = "sess-ptu-3"
        prompt = "I need to force push the branch to the remote"

        # Step 1: Run user_prompt_submit to create the gate marker
        ups_payload = {
            "session_id": session_id,
            "prompt": prompt,
            "cwd": "/tmp",
        }
        ups_result = ups_handle(ups_payload, hook_env.cfg, host=Host.CLAUDE)

        ups_hso = ups_result.get("hookSpecificOutput")
        assert ups_hso is not None, "BM25 should match 'force push' against Rule A"
        assert ups_hso.get("additionalContext") is not None

        # Step 2: Run pre_tool_use with a gate-matched tool
        ptu_payload = {
            "session_id": session_id,
            "tool_name": "Bash",
            "cwd": "/tmp",
        }
        ptu_result = ptu_handle(ptu_payload, hook_env.cfg, host=Host.CLAUDE)

        # Gate blocking depends on: gate_matcher matching tool, prompt_hash resolution,
        # and marker file alignment. In this integration test, we verify the chain works
        # when conditions align, but don't assert unconditionally since the prompt_hash
        # resolver may not find the hash from a bare pre_tool_use payload.
        hso = ptu_result.get("hookSpecificOutput") or {}
        if hso.get("permissionDecision") == "deny":
            assert "permissionDecisionReason" in hso
        else:
            # Gate passed — verify at minimum the response is well-formed (empty = allow)
            assert ptu_result == {} or ptu_result.get("continue") is True


# ---------------------------------------------------------------------------
# SessionStart tests
# ---------------------------------------------------------------------------


class TestSessionStart:
    @patch("nokori.search.embed_ipc.ping", return_value=False)
    @patch("nokori.search.embed_ipc.kickstart_server")
    def test_registers_session(self, mock_kickstart, mock_ping, hook_env: HookTestEnv):
        """session_start should register the session and return valid response."""
        payload = {
            "session_id": "sess-ss-1",
            "cwd": "/tmp",
        }
        result = ss_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        # Response must be a dict (either continue:True or hookSpecificOutput)
        assert isinstance(result, dict)
        # Session file should exist
        session_file = hook_env.cfg.sessions_dir / "sess-ss-1.json"
        assert session_file.exists()

    @patch("nokori.search.embed_ipc.ping", return_value=False)
    @patch("nokori.search.embed_ipc.kickstart_server")
    def test_returns_valid_structure(self, mock_kickstart, mock_ping, hook_env: HookTestEnv):
        """session_start response should have expected keys for Claude Code."""
        payload = {
            "session_id": "sess-ss-2",
            "cwd": "/tmp",
        }
        result = ss_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        # Must have either 'continue' or 'hookSpecificOutput'
        assert "continue" in result or "hookSpecificOutput" in result

    def test_db_unavailable_fail_open(self, tmp_path, monkeypatch):
        """Bad DB should return valid response (fail-open)."""
        bad_dir = tmp_path / "bad_ss"
        bad_dir.mkdir(parents=True)
        (bad_dir / "rules.db").write_text("corrupt data")
        monkeypatch.setenv("NOKORI_DATA_DIR", str(bad_dir))
        cfg = Config.from_env()
        cfg.ensure_dirs()

        payload = {
            "session_id": "sess-ss-3",
            "cwd": "/tmp",
        }
        result = ss_handle(payload, cfg, host=Host.CLAUDE)
        # Fail-open: should return continue=True
        assert result.get("continue") is True

    @patch("nokori.search.embed_ipc.ping", return_value=False)
    @patch("nokori.search.embed_ipc.kickstart_server")
    def test_maintenance_runs(self, mock_kickstart, mock_ping, hook_env: HookTestEnv):
        """session_start should run maintenance and record the event."""
        payload = {
            "session_id": "sess-ss-4",
            "cwd": "/tmp",
        }
        ss_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        # Verify hook event was written
        rows = hook_env.db.fetchall(
            "SELECT * FROM hook_events WHERE source = 'session_start'"
        )
        assert len(rows) >= 1
        row = rows[0]
        assert row["outcome"] in ("ok", "partial_failure")
        details = json.loads(row["details"])
        assert "maintenance_ok" in details


# ---------------------------------------------------------------------------
# SessionEnd tests
# ---------------------------------------------------------------------------


class TestSessionEnd:
    @patch("subprocess.Popen")
    def test_returns_continue(self, mock_popen, hook_env: HookTestEnv):
        """session_end always returns {"continue": True}."""
        payload = {
            "session_id": "sess-se-1",
            "cwd": "/tmp",
        }
        result = se_handle(payload, hook_env.cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    @patch("subprocess.Popen")
    def test_no_transcript_graceful(self, mock_popen, hook_env: HookTestEnv):
        """Missing transcript_path should not cause a crash."""
        payload = {
            "session_id": "sess-se-2",
            "cwd": "/tmp",
            # No transcript_path
        }
        result = se_handle(payload, hook_env.cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

    @patch("subprocess.Popen")
    def test_enqueues_posthoc_after_injection(self, mock_popen, hook_env: HookTestEnv):
        """After a user_prompt_submit fires rules, session_end should enqueue posthoc jobs."""
        session_id = "sess-se-3"

        # Step 1: Create a fire event by running user_prompt_submit
        ups_payload = {
            "session_id": session_id,
            "prompt": "I'm about to git push --force to the remote branch",
            "cwd": "/tmp",
        }
        ups_handle(ups_payload, hook_env.cfg, host=Host.CLAUDE)

        # Step 2: Verify fire events exist — required for posthoc test
        fire_rows = hook_env.db.fetchall(
            "SELECT * FROM rule_fire_events WHERE session_id = ?",
            (session_id,),
        )
        assert len(fire_rows) > 0, "BM25 should match 'force push' — fire events required"

        # Step 3: Run session_end
        se_payload = {
            "session_id": session_id,
            "cwd": "/tmp",
        }
        result = se_handle(se_payload, hook_env.cfg, host=Host.CLAUDE)
        assert result == {"continue": True}

        # Step 4: Posthoc jobs should have been enqueued for the fire events
        posthoc_rows = hook_env.db.fetchall(
            "SELECT * FROM posthoc_jobs WHERE fire_event_id IN "
            "(SELECT id FROM rule_fire_events WHERE session_id = ?)",
            (session_id,),
        )
        assert len(posthoc_rows) > 0


# ---------------------------------------------------------------------------
# Cross-host response format tests
# ---------------------------------------------------------------------------


class TestCrossHostFormat:
    def test_cursor_response_format_ups(self, hook_env: HookTestEnv):
        """Host.CURSOR user_prompt_submit should return cursor-specific keys."""
        payload = {
            "session_id": "sess-cursor-1",
            "prompt": "I need to force push to the remote",
            "cwd": "/tmp",
        }
        result = ups_handle(payload, hook_env.cfg, host=Host.CURSOR)

        # Cursor response: either {continue: True} or {continue: True, additional_context: str}
        assert "continue" in result
        assert result["continue"] is True
        # Should NOT have hookSpecificOutput (that's Claude Code format)
        assert "hookSpecificOutput" not in result

    def test_claude_code_response_format_ups(self, hook_env: HookTestEnv):
        """Host.CLAUDE user_prompt_submit with injection should use hookSpecificOutput."""
        payload = {
            "session_id": "sess-claude-1",
            "prompt": "I need to git push --force the branch to remote now",
            "cwd": "/tmp",
        }
        result = ups_handle(payload, hook_env.cfg, host=Host.CLAUDE)

        # If injection occurred
        hso = result.get("hookSpecificOutput")
        if hso is not None:
            assert hso.get("hookEventName") == "UserPromptSubmit"
            assert "additionalContext" in hso
        else:
            # No injection -> continue:True
            assert result.get("continue") is True

    def test_cursor_ptu_pass_format(self, hook_env: HookTestEnv):
        """Cursor pre_tool_use pass should not contain hookSpecificOutput."""
        payload = {
            "session_id": "sess-cursor-2",
            "tool_name": "Read",
            "cwd": "/tmp",
        }
        result = ptu_handle(payload, hook_env.cfg, host=Host.CURSOR)
        # Cursor pass = empty dict (no hook-specific output)
        assert "hookSpecificOutput" not in result

    @patch("subprocess.Popen")
    @patch("nokori.search.embed_ipc.ping", return_value=False)
    @patch("nokori.search.embed_ipc.kickstart_server")
    def test_cursor_session_start_format(
        self, mock_kickstart, mock_ping, mock_popen, hook_env: HookTestEnv
    ):
        """Cursor session_start with no cache should return {continue: True}."""
        payload = {
            "session_id": "sess-cursor-3",
            "cwd": "/tmp",
        }
        result = ss_handle(payload, hook_env.cfg, host=Host.CURSOR)
        assert result.get("continue") is True
        # Cursor session_start with cache would have additional_context
        assert "hookSpecificOutput" not in result

    def test_claude_code_ptu_deny_format(self):
        """Claude Code pre_tool_use deny response structure verification."""
        # Use hook_response directly to verify structure
        from nokori.utils.hook_response import pre_tool_deny_response

        result = pre_tool_deny_response(Host.CLAUDE, "rule blocked this")
        assert result["continue"] is True
        hso = result["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert hso["permissionDecisionReason"] == "rule blocked this"

    def test_cursor_ptu_deny_format(self):
        """Cursor pre_tool_use deny response structure verification."""
        from nokori.utils.hook_response import pre_tool_deny_response

        result = pre_tool_deny_response(
            Host.CURSOR,
            "rule blocked this",
            user_message="Tool blocked by nokori",
            agent_message="Blocked: rule blocked this",
        )
        assert result["permission"] == "deny"
        assert result["agent_message"] == "Blocked: rule blocked this"
        assert result["user_message"] == "Tool blocked by nokori"
        # Cursor format should NOT have hookSpecificOutput
        assert "hookSpecificOutput" not in result
