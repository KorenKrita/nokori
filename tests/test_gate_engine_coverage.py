"""Coverage tests for gate engine paths not covered by test_gate_engine.py.

Covers: expired markers, empty markers, hash mismatch, processing errors,
tool_input_exclusion_fires, rule version mismatch, runtime_policy_version mismatch.
"""

import json
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.gate.engine import GateEngine, has_tool_evidence, tool_input_exclusion_fires
from nokori.gate.marker import MarkerRule, write as write_marker
from nokori.policy import RUNTIME_POLICY_VERSION


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "status, severity, trigger_canonical, action_instruction, "
            "runtime_policy_version, created_by_pipeline_version, "
            "source_origin, project_scope, excluded_contexts, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "gate-rule-1", "gate01", 6, 1,
                "trusted", "gate_eligible",
                "force push shared branch", "use lease instead",
                RUNTIME_POLICY_VERSION, "1.0.0",
                "transcript_extraction", "global",
                json.dumps([{"id": "exc-deploy", "label": "deploy", "patterns": ["deploy pipeline"], "scope": "tool_input_only"}]),
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
            ),
        )
    yield cfg, db
    db.close()


def _marker_rule(short_id="gate01", **kwargs):
    defaults = dict(
        short_id=short_id,
        action="use lease instead",
        trigger="force push shared branch",
        source_type="transcript_extraction",
        rule_id="gate-rule-1",
        status="trusted",
        severity="gate_eligible",
        rule_version=1,
        runtime_policy_version=RUNTIME_POLICY_VERSION,
    )
    defaults.update(kwargs)
    return MarkerRule(**defaults)


class TestGateExpiredMarker:
    def test_expired_marker_returns_not_blocked(self, gate_env):
        cfg, db = gate_env
        ph = "expiredmarker12345"
        write_marker(cfg, "sess-exp", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        with patch("nokori.gate.engine.marker_io.is_expired", return_value=True):
            decision = engine.should_block(
                tool_name="Bash",
                prompt_hash=ph,
                session_id="sess-exp",
                payload={"tool_name": "Bash"},
            )
        assert not decision.blocked
        assert decision.reason == "marker_expired"


class TestGateEmptyMarker:
    def test_empty_rules_marker_returns_not_blocked(self, gate_env):
        cfg, db = gate_env
        ph = "emptymarker123456"
        write_marker(cfg, "sess-empty", "force push", [], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-empty",
            payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.reason == "empty_marker"


class TestGateNoPromptHash:
    def test_no_prompt_hash_returns_not_blocked(self, gate_env):
        cfg, db = gate_env
        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=None,
            session_id="sess-1",
            payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.reason == "no_prompt_hash"


class TestGateRuleVersionMismatch:
    def test_stale_rule_version_not_eligible(self, gate_env):
        cfg, db = gate_env
        ph = "staleversion12345"
        write_marker(cfg, "sess-ver", "force push", [_marker_rule(rule_version=99)], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-ver",
            payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.reason == "no_eligible_rules"


class TestGateRuntimePolicyMismatch:
    def test_stale_policy_version_not_eligible(self, gate_env):
        cfg, db = gate_env
        ph = "stalepolicy12345"
        write_marker(
            cfg, "sess-pol", "force push",
            [_marker_rule(runtime_policy_version="old-version")],
            ph=ph,
        )

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-pol",
            payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.reason == "no_eligible_rules"


class TestToolInputExclusion:
    def test_exclusion_fires_when_tool_input_matches_pattern(self):
        rule = _marker_rule()
        excluded = [{"id": "exc-deploy", "label": "deploy", "patterns": ["deploy pipeline"], "scope": "tool_input_only"}]
        result = tool_input_exclusion_fires(
            rule,
            {"tool_input": "run deploy pipeline for staging"},
            excluded,
        )
        assert result is True

    def test_exclusion_does_not_fire_without_match(self):
        rule = _marker_rule()
        excluded = [{"id": "exc-deploy", "label": "deploy", "patterns": ["deploy pipeline"], "scope": "tool_input_only"}]
        result = tool_input_exclusion_fires(
            rule,
            {"tool_input": "git push --force origin main"},
            excluded,
        )
        assert result is False

    def test_exclusion_ignores_non_tool_input_scope(self):
        rule = _marker_rule()
        excluded = [{"id": "exc-prompt", "label": "prompt scope", "patterns": ["deploy"], "scope": "prompt_only"}]
        result = tool_input_exclusion_fires(
            rule,
            {"tool_input": "deploy pipeline for staging"},
            excluded,
        )
        assert result is False

    def test_exclusion_no_tool_input_returns_false(self):
        rule = _marker_rule()
        excluded = [{"id": "exc-deploy", "label": "deploy", "patterns": ["deploy"], "scope": "tool_input_only"}]
        result = tool_input_exclusion_fires(rule, {}, excluded)
        assert result is False

    def test_exclusion_empty_excluded_contexts_returns_false(self):
        rule = _marker_rule()
        result = tool_input_exclusion_fires(
            rule,
            {"tool_input": "deploy pipeline"},
            [],
        )
        assert result is False


class TestToolEvidence:
    def test_no_tool_input_always_passes(self):
        rule = _marker_rule()
        assert has_tool_evidence(rule, {}) is True

    def test_matching_trigger_in_tool_input(self):
        rule = _marker_rule()
        assert has_tool_evidence(rule, {"tool_input": "force push shared branch"}) is True

    def test_partial_token_match_passes(self):
        rule = _marker_rule()
        assert has_tool_evidence(rule, {"tool_input": "git push --force to shared branch"}) is True

    def test_completely_unrelated_input_fails(self):
        rule = _marker_rule(trigger="deploy kubernetes cluster", action="use rolling update")
        assert has_tool_evidence(rule, {"tool_input": "cat README.md"}) is False

    def test_dict_tool_input_serialized(self):
        rule = _marker_rule()
        assert has_tool_evidence(
            rule,
            {"tool_input": {"command": "git push --force shared branch"}},
        ) is True


class TestGateProcessingError:
    def test_processing_error_consumes_marker_and_passes(self, gate_env):
        cfg, db = gate_env
        ph = "procerror12345678"
        write_marker(cfg, "sess-err", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        with patch("nokori.gate.engine.is_gate_eligible_rule", side_effect=RuntimeError("db error")):
            decision = engine.should_block(
                tool_name="Bash",
                prompt_hash=ph,
                session_id="sess-err",
                payload={"tool_name": "Bash"},
            )
        assert not decision.blocked
        assert decision.reason == "processing_error"
