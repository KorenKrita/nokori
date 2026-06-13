"""Tests for gate marker lifecycle state machine — covers each terminal state,
observability events, batch eligibility, PromptHashResolver, and deferral detection.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.events.observability import query_events
from nokori.gate import marker as marker_io
from nokori.gate.engine import GateEngine, _batch_check_eligibility, is_gate_eligible_rule
from nokori.gate.marker import (
    MarkerRule,
    PromptHashResolver,
    prompt_hash,
    read_latest_marker,
    write as write_marker,
)
from nokori.gate.state import MarkerState
from nokori.hooks.pre_tool_use import handle
from nokori.policy import RUNTIME_POLICY_VERSION
from nokori.utils.host import Host


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _old_iso(seconds_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


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
                json.dumps([]),
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


class TestMarkerStateConsumed:
    """Terminal state: consumed — marker matched, tool blocked."""

    def test_consumed_state_on_successful_block(self, gate_env):
        cfg, db = gate_env
        ph = "consumed123456789"
        write_marker(cfg, "sess-consumed", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-consumed", payload={"tool_name": "Bash"},
        )
        assert decision.blocked
        assert decision.state == MarkerState.consumed
        assert decision.rules_checked == 1
        assert decision.rules_blocked == 1
        assert decision.elapsed_ms > 0

    def test_consumed_writes_event_via_handle(self, gate_env):
        cfg, db = gate_env
        ph = prompt_hash("force push shared branch")
        write_marker(cfg, "sess-ev", "force push shared branch", [_marker_rule()], ph=ph)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-ev", "gate-rule-1", "sess-ev", ph, "hot", _utcnow_iso()),
            )

        out = handle(
            {"session_id": "sess-ev", "tool_name": "Bash", "prompt": "force push shared branch"},
            cfg, host=Host.CLAUDE,
        )
        hso = out.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") == "deny"

        events = query_events(db, session_id="sess-ev", source="gate_marker_resolved")
        assert len(events) == 1
        assert events[0]["outcome"] == "consumed"

    def test_consumed_deletes_marker(self, gate_env):

        cfg, db = gate_env
        ph = "consumedel123456"
        write_marker(cfg, "sess-del", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-del", payload={"tool_name": "Bash"},
        )
        assert marker_io.read(cfg, "sess-del", prompt_hash_value=ph) is None


class TestMarkerStateExpired:
    """Terminal state: expired — TTL exceeded."""

    def test_expired_state(self, gate_env):
        cfg, db = gate_env
        ph = "expired1234567890"
        write_marker(cfg, "sess-exp", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        with patch("nokori.gate.engine.marker_io.is_expired", return_value=True):
            decision = engine.should_block(
                tool_name="Bash", prompt_hash=ph,
                session_id="sess-exp", payload={"tool_name": "Bash"},
            )
        assert not decision.blocked
        assert decision.state == MarkerState.expired
        assert decision.elapsed_ms > 0

    def test_expired_writes_event(self, gate_env):
        cfg, db = gate_env
        ph = prompt_hash("force push shared branch")
        write_marker(cfg, "sess-exp-ev", "force push shared branch", [_marker_rule()], ph=ph)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-exp-ev", "gate-rule-1", "sess-exp-ev", ph, "hot", _utcnow_iso()),
            )

        with patch("nokori.gate.engine.marker_io.is_expired", return_value=True):
            handle(
                {"session_id": "sess-exp-ev", "tool_name": "Bash", "prompt": "force push shared branch"},
                cfg, host=Host.CLAUDE,
            )

        events = query_events(db, session_id="sess-exp-ev", source="gate_marker_resolved")
        assert len(events) == 1
        assert events[0]["outcome"] == "expired"

    def test_expired_deletes_marker(self, gate_env):

        cfg, db = gate_env
        ph = "expireddel1234567"
        write_marker(cfg, "sess-exp-d", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        with patch("nokori.gate.engine.marker_io.is_expired", return_value=True):
            engine.should_block(
                tool_name="Bash", prompt_hash=ph,
                session_id="sess-exp-d", payload={"tool_name": "Bash"},
            )
        assert marker_io.read(cfg, "sess-exp-d", prompt_hash_value=ph) is None


class TestMarkerStateIneligible:
    """Terminal state: ineligible — all rules failed eligibility checks."""

    def test_ineligible_state(self, gate_env):
        cfg, db = gate_env
        ph = "ineligible1234567"
        # Use a rule_version mismatch to make it ineligible
        write_marker(cfg, "sess-ine", "force push", [_marker_rule(rule_version=99)], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-ine", payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.state == MarkerState.ineligible
        assert decision.rules_checked == 1
        assert decision.rules_blocked == 0

    def test_ineligible_writes_event(self, gate_env):
        cfg, db = gate_env
        ph = prompt_hash("force push shared branch")
        write_marker(cfg, "sess-ine-ev", "force push shared branch", [_marker_rule(rule_version=99)], ph=ph)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-ine-ev", "gate-rule-1", "sess-ine-ev", ph, "hot", _utcnow_iso()),
            )

        handle(
            {"session_id": "sess-ine-ev", "tool_name": "Bash", "prompt": "force push shared branch"},
            cfg, host=Host.CLAUDE,
        )

        events = query_events(db, session_id="sess-ine-ev", source="gate_marker_resolved")
        assert len(events) == 1
        assert events[0]["outcome"] == "ineligible"

    def test_ineligible_deletes_marker(self, gate_env):

        cfg, db = gate_env
        ph = "ineligdel12345678"
        write_marker(cfg, "sess-ine-d", "force push", [_marker_rule(rule_version=99)], ph=ph)

        engine = GateEngine(cfg, db)
        engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-ine-d", payload={"tool_name": "Bash"},
        )
        assert marker_io.read(cfg, "sess-ine-d", prompt_hash_value=ph) is None


class TestMarkerStateHashMismatch:
    """Terminal state: hash_mismatch — marker for different prompt turn."""

    def test_hash_mismatch_state(self, gate_env):
        cfg, db = gate_env
        ph_marker = "markerphash123456"
        ph_current = "currentphash12345"
        write_marker(cfg, "sess-hm", "force push", [_marker_rule()], ph=ph_marker)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph_current,
            session_id="sess-hm", payload={"tool_name": "Bash"},
        )
        # no_marker because read(ph_current) returns None — marker is under ph_marker
        assert not decision.blocked
        assert decision.reason == "no_marker"

    def test_hash_mismatch_state_via_engine(self, gate_env):
        """Hash mismatch when marker exists but prompt_hash doesn't match."""
        cfg, db = gate_env
        ph = "hashmismatch12345"
        write_marker(cfg, "sess-hm2", "force push", [_marker_rule()], ph=ph)

        # Overwrite marker with different prompt_hash in body to trigger mismatch
        marker_path = cfg.marker_path("sess-hm2", ph)
        data = json.loads(marker_path.read_text())
        data["prompt_hash"] = "differenthash000"
        marker_path.write_text(json.dumps(data))

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-hm2", payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.state == MarkerState.hash_mismatch


class TestMarkerStateEmpty:
    """Terminal state: empty — zero rules in marker."""

    def test_empty_state(self, gate_env):
        cfg, db = gate_env
        ph = "emptystate1234567"
        write_marker(cfg, "sess-emp", "force push", [], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-emp", payload={"tool_name": "Bash"},
        )
        assert not decision.blocked
        assert decision.state == MarkerState.empty

    def test_empty_writes_event(self, gate_env):
        cfg, db = gate_env
        ph = prompt_hash("force push shared branch")
        write_marker(cfg, "sess-emp-ev", "force push shared branch", [], ph=ph)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-emp-ev", "gate-rule-1", "sess-emp-ev", ph, "hot", _utcnow_iso()),
            )

        handle(
            {"session_id": "sess-emp-ev", "tool_name": "Bash", "prompt": "force push shared branch"},
            cfg, host=Host.CLAUDE,
        )

        events = query_events(db, session_id="sess-emp-ev", source="gate_marker_resolved")
        assert len(events) == 1
        assert events[0]["outcome"] == "empty"

    def test_empty_deletes_marker(self, gate_env):

        cfg, db = gate_env
        ph = "emptydel12345678"
        write_marker(cfg, "sess-emp-d", "force push", [], ph=ph)

        engine = GateEngine(cfg, db)
        engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-emp-d", payload={"tool_name": "Bash"},
        )
        assert marker_io.read(cfg, "sess-emp-d", prompt_hash_value=ph) is None


class TestMarkerStateError:
    """Terminal state: error — exception during processing."""

    def test_error_state(self, gate_env):
        cfg, db = gate_env
        ph = "errorstate123456"
        write_marker(cfg, "sess-err", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        with patch("nokori.gate.engine._batch_check_eligibility", side_effect=RuntimeError("db error")):
            decision = engine.should_block(
                tool_name="Bash", prompt_hash=ph,
                session_id="sess-err", payload={"tool_name": "Bash"},
            )
        assert not decision.blocked
        assert decision.state == MarkerState.error
        assert decision.rules_checked == 1

    def test_error_writes_event(self, gate_env):
        cfg, db = gate_env
        ph = prompt_hash("force push shared branch")
        write_marker(cfg, "sess-err-ev", "force push shared branch", [_marker_rule()], ph=ph)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-err-ev", "gate-rule-1", "sess-err-ev", ph, "hot", _utcnow_iso()),
            )

        with patch("nokori.gate.engine._batch_check_eligibility", side_effect=RuntimeError("db error")):
            handle(
                {"session_id": "sess-err-ev", "tool_name": "Bash", "prompt": "force push shared branch"},
                cfg, host=Host.CLAUDE,
            )

        events = query_events(db, session_id="sess-err-ev", source="gate_marker_resolved")
        assert len(events) == 1
        assert events[0]["outcome"] == "error"

    def test_error_deletes_marker(self, gate_env):

        cfg, db = gate_env
        ph = "errordel123456789"
        write_marker(cfg, "sess-err-d", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        with patch("nokori.gate.engine._batch_check_eligibility", side_effect=RuntimeError("boom")):
            engine.should_block(
                tool_name="Bash", prompt_hash=ph,
                session_id="sess-err-d", payload={"tool_name": "Bash"},
            )
        assert marker_io.read(cfg, "sess-err-d", prompt_hash_value=ph) is None


class TestBatchEligibility:
    """Batch eligibility returns same results as sequential is_gate_eligible_rule."""

    def test_batch_matches_sequential_single_rule(self, gate_env):
        _, db = gate_env
        rule = _marker_rule()
        batch_results = _batch_check_eligibility([rule], db)
        seq_eligible, seq_excluded = is_gate_eligible_rule(rule, db)

        assert len(batch_results) == 1
        _, batch_eligible, batch_excluded = batch_results[0]
        assert batch_eligible == seq_eligible
        assert batch_excluded == seq_excluded

    def test_batch_matches_sequential_multiple_rules(self, gate_env):
        _, db = gate_env
        rules = [
            _marker_rule(),
            _marker_rule(rule_version=99),  # version mismatch -> ineligible
            _marker_rule(short_id="missing", rule_id=None),  # unknown rule
        ]
        batch_results = _batch_check_eligibility(rules, db)
        assert len(batch_results) == 3

        for i, rule in enumerate(rules):
            seq_eligible, seq_excluded = is_gate_eligible_rule(rule, db)
            _, batch_eligible, batch_excluded = batch_results[i]
            assert batch_eligible == seq_eligible, f"rule {i} eligibility mismatch"
            assert batch_excluded == seq_excluded, f"rule {i} excluded_contexts mismatch"

    def test_batch_empty_rules(self, gate_env):
        _, db = gate_env
        assert _batch_check_eligibility([], db) == []


class TestPromptHashResolver:
    """PromptHashResolver three-layer fallback chain."""

    def test_resolves_from_payload(self, gate_env):
        cfg, db = gate_env
        resolver = PromptHashResolver(cfg, "sess-res", db)
        ph_val, source = resolver.resolve({"prompt": "hello world"}, None)
        assert ph_val is not None
        assert source == "payload"

    def test_resolves_from_disk_marker(self, gate_env):
        cfg, db = gate_env
        ph = "diskmarkerph12345"
        write_marker(cfg, "sess-disk", "force push", [_marker_rule()], ph=ph)
        # Insert injection so disk marker is valid
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-disk", "gate-rule-1", "sess-disk", ph, "hot", _utcnow_iso()),
            )
        on_disk = read_latest_marker(cfg, "sess-disk")

        resolver = PromptHashResolver(cfg, "sess-disk", db)
        ph_val, source = resolver.resolve({}, on_disk)
        assert ph_val == ph
        assert source == "disk_marker"

    def test_resolves_from_fire_events(self, gate_env):
        cfg, db = gate_env
        ph = "fireeventsph12345"
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-fire", "gate-rule-1", "sess-fire", ph, "hot", _utcnow_iso()),
            )
        resolver = PromptHashResolver(cfg, "sess-fire", db)
        ph_val, source = resolver.resolve({}, None)
        assert ph_val == ph
        assert source == "fire_events"

    def test_resolves_none_when_all_layers_fail(self, gate_env):
        cfg, db = gate_env
        resolver = PromptHashResolver(cfg, "sess-none", db)
        ph_val, source = resolver.resolve({}, None)
        assert ph_val is None
        assert source == "none"

    def test_disk_marker_without_injection_falls_through(self, gate_env):
        """Disk marker without injection_exists should fall through to fire_events."""
        cfg, db = gate_env
        ph_disk = "disknoinjection01"
        ph_fire = "fireeventph012345"
        write_marker(cfg, "sess-fallthru", "force push", [_marker_rule()], ph=ph_disk)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-fallthru", "gate-rule-1", "sess-fallthru", ph_fire, "hot", _utcnow_iso()),
            )
        on_disk = read_latest_marker(cfg, "sess-fallthru")

        resolver = PromptHashResolver(cfg, "sess-fallthru", db)
        ph_val, source = resolver.resolve({}, on_disk)
        assert ph_val == ph_fire
        assert source == "fire_events"
        # Verify stale disk marker was cleaned up by resolver side-effect
        assert marker_io.read(cfg, "sess-fallthru", prompt_hash_value=ph_disk) is None


class TestDeferralDetection:
    """Deferral flag when marker is >2s old at consumed resolution."""

    def test_deferred_true_when_marker_old(self, gate_env):
        cfg, db = gate_env
        ph = "deferredold123456"
        write_marker(cfg, "sess-def", "force push", [_marker_rule()], ph=ph)
        # Overwrite created_at to be 5 seconds ago
        marker_path = cfg.marker_path("sess-def", ph)
        data = json.loads(marker_path.read_text())
        data["created_at"] = _old_iso(5.0)
        marker_path.write_text(json.dumps(data))

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-def", payload={"tool_name": "Bash"},
        )
        assert decision.blocked
        assert decision.state == MarkerState.consumed
        assert decision.deferred is True

    def test_deferred_false_when_marker_fresh(self, gate_env):
        cfg, db = gate_env
        ph = "deferredfresh1234"
        write_marker(cfg, "sess-fresh", "force push", [_marker_rule()], ph=ph)

        engine = GateEngine(cfg, db)
        decision = engine.should_block(
            tool_name="Bash", prompt_hash=ph,
            session_id="sess-fresh", payload={"tool_name": "Bash"},
        )
        assert decision.blocked
        assert decision.state == MarkerState.consumed
        assert decision.deferred is False
