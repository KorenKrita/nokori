"""Integration tests for GateEngine — verifies gate decisions through the public interface."""

import pytest

from nokori.config import Config
from nokori.db import open_db
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
            "source_origin, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "gate-rule-1", "gate01", 6, 1,
                "trusted", "gate_eligible",
                "force push shared branch", "use lease instead",
                RUNTIME_POLICY_VERSION, "1.0.0",
                "transcript_extraction", "global",
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


class TestGateBlocks:
    def test_gate_blocks_matching_tool_with_valid_marker(self, gate_env):
        from nokori.gate.engine import GateEngine

        cfg, db = gate_env
        ph = "abcdef1234567890"
        write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-1",
            payload={"tool_name": "Bash"},
        )
        assert decision.blocked
        assert "gate01" in [r.short_id for r in decision.rules]

    def test_gate_passes_when_no_marker(self, gate_env):
        from nokori.gate.engine import GateEngine

        cfg, db = gate_env
        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash="nonexistent00000",
            session_id="sess-no-marker",
            payload={"tool_name": "Bash"},
        )
        assert not decision.blocked

    def test_gate_passes_when_gate_disabled(self, tmp_path, monkeypatch):
        from nokori.gate.engine import GateEngine

        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_GATE_ENABLED", "0")
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            ph = "abcdef1234567890"
            write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

            engine = GateEngine(cfg, db)
            decision = engine.should_block(
                tool_name="Bash",
                prompt_hash=ph,
                session_id="sess-1",
                payload={"tool_name": "Bash"},
            )
            assert not decision.blocked
        finally:
            db.close()

    def test_gate_passes_when_tool_does_not_match(self, gate_env):
        from nokori.gate.engine import GateEngine

        cfg, db = gate_env
        ph = "abcdef1234567890"
        write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Read",
            prompt_hash=ph,
            session_id="sess-1",
            payload={"tool_name": "Read"},
        )
        assert not decision.blocked

    def test_gate_passes_when_rule_no_longer_trusted(self, gate_env):
        from nokori.gate.engine import GateEngine

        cfg, db = gate_env
        with db.transaction() as tx:
            tx.execute("UPDATE rules SET status='active' WHERE id='gate-rule-1'")

        ph = "abcdef1234567890"
        write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-1",
            payload={"tool_name": "Bash"},
        )
        assert not decision.blocked

    def test_gate_consumes_marker_on_block(self, gate_env):
        from nokori.gate.engine import GateEngine
        from nokori.gate import marker as marker_io

        cfg, db = gate_env
        ph = "abcdef1234567890"
        write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-1",
            payload={"tool_name": "Bash"},
        )
        assert decision.blocked
        assert marker_io.read(cfg, "sess-1", prompt_hash_value=ph) is None

    def test_gate_blocks_with_matching_tool_input(self, gate_env):
        from nokori.gate.engine import GateEngine

        cfg, db = gate_env
        ph = "abcdef1234567890"
        write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-1",
            payload={"tool_name": "Bash", "tool_input": "force push shared branch to remote"},
        )
        assert decision.blocked

    def test_gate_passes_when_tool_input_unrelated(self, gate_env):
        from nokori.gate.engine import GateEngine

        cfg, db = gate_env
        ph = "abcdef1234567890"
        write_marker(cfg, "sess-1", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash",
            prompt_hash=ph,
            session_id="sess-1",
            payload={"tool_name": "Bash", "tool_input": "cat readme.md"},
        )
        assert not decision.blocked
