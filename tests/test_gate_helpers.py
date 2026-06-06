"""Unit tests for gate helper functions in nokori/hooks/pre_tool_use.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from nokori.db import open_db
from nokori.gate.marker import MarkerRule
from nokori.hooks.pre_tool_use import (
    _has_tool_evidence,
    _is_gate_eligible_rule,
    _tool_input_exclusion_fires,
)
from nokori.policy import RUNTIME_POLICY_VERSION


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(
    db,
    *,
    rule_id: str = "r-test-1",
    short_id: str = "abc123",
    status: str = "trusted",
    severity: str = "gate_eligible",
    rule_version: int = 1,
    runtime_policy_version: str = RUNTIME_POLICY_VERSION,
    excluded_contexts: str | None = "[]",
):
    now = _now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, status, severity, rule_version, "
            "runtime_policy_version, excluded_contexts, "
            "trigger_canonical, action_instruction, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id, short_id, status, severity, rule_version,
                runtime_policy_version, excluded_contexts,
                "test trigger", "test action", now, now,
            ),
        )


@pytest.fixture
def db(tmp_path):
    database = open_db(tmp_path / "rules.db")
    yield database
    database.close()


# ---------------------------------------------------------------------------
# _is_gate_eligible_rule
# ---------------------------------------------------------------------------


class TestIsGateEligibleRule:
    def test_trusted_gate_eligible_returns_true(self, db):
        _insert_rule(db, excluded_contexts='[{"id": "ex1", "scope": "tool_input_only", "patterns": ["foo"]}]')
        rule = MarkerRule(
            short_id="abc123",
            rule_id="r-test-1",
            action="test action",
            trigger="test trigger",
            rule_version=1,
            runtime_policy_version=RUNTIME_POLICY_VERSION,
        )
        eligible, contexts = _is_gate_eligible_rule(rule, db)
        assert eligible is True
        assert contexts == [{"id": "ex1", "scope": "tool_input_only", "patterns": ["foo"]}]

    def test_active_status_returns_false(self, db):
        _insert_rule(db, status="active")
        rule = MarkerRule(
            short_id="abc123",
            rule_id="r-test-1",
            action="test action",
            trigger="test trigger",
            rule_version=1,
            runtime_policy_version=RUNTIME_POLICY_VERSION,
        )
        eligible, contexts = _is_gate_eligible_rule(rule, db)
        assert eligible is False
        assert contexts is None

    def test_trusted_but_not_gate_eligible_severity(self, db):
        _insert_rule(db, severity="high_risk")
        rule = MarkerRule(
            short_id="abc123",
            rule_id="r-test-1",
            action="test action",
            trigger="test trigger",
            rule_version=1,
            runtime_policy_version=RUNTIME_POLICY_VERSION,
        )
        eligible, contexts = _is_gate_eligible_rule(rule, db)
        assert eligible is False
        assert contexts is None

    def test_nonexistent_rule_id(self, db):
        rule = MarkerRule(
            short_id="nonexist",
            rule_id="r-does-not-exist",
            action="test action",
            trigger="test trigger",
        )
        eligible, contexts = _is_gate_eligible_rule(rule, db)
        assert eligible is False
        assert contexts is None

    def test_empty_excluded_contexts(self, db):
        """Empty string excluded_contexts (schema default) yields empty list."""
        _insert_rule(db, excluded_contexts="")
        rule = MarkerRule(
            short_id="abc123",
            rule_id="r-test-1",
            action="test action",
            trigger="test trigger",
            rule_version=1,
            runtime_policy_version=RUNTIME_POLICY_VERSION,
        )
        eligible, contexts = _is_gate_eligible_rule(rule, db)
        assert eligible is True
        assert contexts == []


# ---------------------------------------------------------------------------
# _has_tool_evidence
# ---------------------------------------------------------------------------


class TestHasToolEvidence:
    def test_trigger_tokens_in_tool_input(self):
        rule = MarkerRule(
            short_id="x1",
            action="use --force-with-lease",
            trigger="force push to shared branch",
        )
        payload = {"tool_input": "git push --force to the shared branch now"}
        assert _has_tool_evidence(rule, payload) is True

    def test_action_tokens_in_tool_input(self):
        rule = MarkerRule(
            short_id="x2",
            action="use --force-with-lease",
            trigger="never force push",
        )
        payload = {"tool_input": "git push --force-with-lease origin main"}
        assert _has_tool_evidence(rule, payload) is True

    def test_no_overlap_returns_false(self):
        rule = MarkerRule(
            short_id="x3",
            action="use --force-with-lease",
            trigger="force push to shared branch",
        )
        payload = {"tool_input": "echo hello world"}
        assert _has_tool_evidence(rule, payload) is False

    def test_empty_tokens_returns_true(self):
        """When trigger and action are too short to produce tokens, returns True."""
        rule = MarkerRule(
            short_id="x4",
            action="ab",
            trigger="cd",
        )
        payload = {"tool_input": "anything at all unrelated content"}
        assert _has_tool_evidence(rule, payload) is True

    def test_large_tool_input_truncated(self):
        """Token matching uses truncated haystack; tokens only in tail don't match."""
        rule = MarkerRule(
            short_id="x5",
            # Phrases that won't match as full substrings in the filler,
            # but produce tokens that only appear past 8000 chars.
            action="deploy canary_release rollback_strategy",
            trigger="verify production_health monitoring_dashboard",
        )
        # Filler that doesn't contain any of the tokens
        filler = "x" * 8100
        payload = {"tool_input": filler + " canary_release rollback_strategy production_health monitoring_dashboard"}
        assert _has_tool_evidence(rule, payload) is False

    def test_no_tool_input_returns_true(self):
        """No tool_input field means prompt-only gate is valid."""
        rule = MarkerRule(
            short_id="x6",
            action="use lease",
            trigger="force push",
        )
        payload = {"tool_name": "Bash"}
        assert _has_tool_evidence(rule, payload) is True


# ---------------------------------------------------------------------------
# _tool_input_exclusion_fires
# ---------------------------------------------------------------------------


class TestToolInputExclusionFires:
    def test_pattern_matches_in_tool_input(self):
        rule = MarkerRule(short_id="e1", action="act", trigger="trig")
        excluded_contexts = [
            {
                "id": "ctx-test",
                "label": "test exclusion",
                "scope": "tool_input_only",
                "match_mode": "phrase",
                "patterns": ["safe operation"],
            }
        ]
        payload = {"tool_input": "this is a safe operation for deployment"}
        assert _tool_input_exclusion_fires(rule, payload, excluded_contexts) is True

    def test_no_pattern_matches(self):
        rule = MarkerRule(short_id="e2", action="act", trigger="trig")
        excluded_contexts = [
            {
                "id": "ctx-test",
                "label": "test exclusion",
                "scope": "tool_input_only",
                "match_mode": "phrase",
                "patterns": ["safe operation"],
            }
        ]
        payload = {"tool_input": "git push --force origin main"}
        assert _tool_input_exclusion_fires(rule, payload, excluded_contexts) is False

    def test_empty_excluded_contexts(self):
        rule = MarkerRule(short_id="e3", action="act", trigger="trig")
        payload = {"tool_input": "anything"}
        assert _tool_input_exclusion_fires(rule, payload, []) is False

    def test_invalid_excluded_context_gracefully_skipped(self):
        """Invalid context entries (missing id/patterns) are skipped, not raised."""
        rule = MarkerRule(short_id="e4", action="act", trigger="trig")
        excluded_contexts = [
            {
                "scope": "tool_input_only",
                # Missing 'id' and 'patterns' → CompilationError
            }
        ]
        payload = {"tool_input": "something"}
        # Should not raise; returns False since no valid context matched
        assert _tool_input_exclusion_fires(rule, payload, excluded_contexts) is False

    def test_non_tool_input_only_scope_ignored(self):
        """Only scope=tool_input_only contexts are evaluated."""
        rule = MarkerRule(short_id="e5", action="act", trigger="trig")
        excluded_contexts = [
            {
                "id": "ctx-global",
                "label": "global exclusion",
                "scope": "global",
                "match_mode": "phrase",
                "patterns": ["safe operation"],
            }
        ]
        payload = {"tool_input": "this is a safe operation"}
        assert _tool_input_exclusion_fires(rule, payload, excluded_contexts) is False

    def test_no_tool_input_returns_false(self):
        rule = MarkerRule(short_id="e6", action="act", trigger="trig")
        excluded_contexts = [
            {
                "id": "ctx-test",
                "label": "test",
                "scope": "tool_input_only",
                "match_mode": "phrase",
                "patterns": ["anything"],
            }
        ]
        payload = {"tool_name": "Bash"}
        assert _tool_input_exclusion_fires(rule, payload, excluded_contexts) is False
