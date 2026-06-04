"""Tests for operational inspection features of the Nokori flywheel.

Covers:
1. CLI test command: fielded matches, hard eligibility, ranking utility,
   embedding bucket, state, scores
2. CLI show command: structured fields, versions, source/activation origin,
   scores, lifecycle timestamps
3. CLI list: uses new statuses only (no merged/dormant)
4. CLI status: pending job counts and pool stats
5. Web retrieve API: decision features and penalties
6. Web rules API: structured fields and scores
7. CLI/web expose archive/dismiss ONLY
8. CLI/web do NOT expose manual promote/trust/suppress endpoints
9. Rule detail includes archived fingerprint summary when archived
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from nokori.config import Config
from nokori.db import open_db
from nokori.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = "2026-06-01T00:00:00Z"

_RULE_INSERT_SQL = (
    "INSERT INTO rules ("
    "id, short_id, schema_version, rule_version, "
    "created_by_pipeline_version, runtime_policy_version, last_rewritten_by_role, "
    "status, severity, "
    "trigger_canonical, trigger_canonical_zh, "
    "concepts, required_concept_groups, excluded_contexts, "
    "near_miss_examples, trigger_variants, trigger_variants_zh, search_terms, "
    "action_instruction, action_instruction_zh, "
    "allowed_behavior, forbidden_behavior, "
    "domain_tags, tool_tags, path_patterns, "
    "quality_score, evidence_support_score, specificity_score, retrieval_readiness_score, "
    "observed_usefulness_score, plausible_usefulness_score, false_positive_score, harmful_score, "
    "source_origin, activation_origin, first_observed_useful_at, "
    "trusted_at, suppressed_at, "
    "project_scope, project_id, "
    "archived_reason, replacement_id, "
    "created_at, updated_at"
    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _make_rule_params(
    *,
    rule_id="rule-1",
    short_id="abc1",
    status="active",
    severity="reminder",
    trigger="never force push to main",
    action="use --force-with-lease instead",
    project_scope="global",
    project_id=None,
    source_origin="transcript_extraction",
    activation_origin=None,
    archived_reason=None,
    replacement_id=None,
    first_observed_useful_at=None,
    trusted_at=None,
    suppressed_at=None,
):
    return (
        rule_id, short_id, 1, 2,
        "pipeline-v3", "policy-v2", None,
        status, severity,
        trigger, None,
        '[{"name": "git"}, {"name": "push"}]', '[{"terms": ["git", "force-push"]}]', '[{"context": "rebase-only"}]',
        '["never force-push"]', '["git push --force"]', '[]',
        '{"en": ["force push", "main branch"]}',
        action, None,
        '["prefer lease"]', '["raw force push"]',
        '["git"]', '["cli"]', '["*.sh"]',
        0.85, 0.9, 0.7, 0.8,
        0.75, 0.6, 0.1, 0.05,
        source_origin, activation_origin, first_observed_useful_at,
        trusted_at, suppressed_at,
        project_scope, project_id,
        archived_reason, replacement_id,
        NOW, NOW,
    )


@pytest.fixture
def cfg(tmp_path):
    base = Config.from_env()
    return replace(base, data_dir=tmp_path)


@pytest.fixture
def db_with_rules(cfg):
    """DB with two rules: one active, one archived."""
    db = open_db(cfg.db_path)
    with db.transaction() as tx:
        tx.execute(_RULE_INSERT_SQL, _make_rule_params())
        tx.execute(
            _RULE_INSERT_SQL,
            _make_rule_params(
                rule_id="rule-2",
                short_id="def2",
                status="archived",
                trigger="always run tests before deploy",
                action="run pytest first",
                archived_reason="superseded_by_merge",
                replacement_id="rule-1",
            ),
        )
        tx.execute(
            _RULE_INSERT_SQL,
            _make_rule_params(
                rule_id="rule-3",
                short_id="ghi3",
                status="trusted",
                severity="high_risk",
                trigger="never commit secrets",
                action="use env vars or vault",
                trusted_at="2026-05-15T00:00:00Z",
                first_observed_useful_at="2026-05-10T00:00:00Z",
                source_origin="transcript_extraction",
                activation_origin="cold_fast_lane",
            ),
        )
    yield db
    db.close()


@pytest.fixture
def client(cfg, db_with_rules):
    app = create_app(cfg)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. CLI test command: fielded matches, eligibility, ranking, bucket, state
# ---------------------------------------------------------------------------


class TestCliTestCommand:
    """Test command output structure via direct function invocation."""

    def test_output_contains_fielded_match_details(self, cfg, db_with_rules, capsys):
        from nokori.commands.test import run

        args = argparse.Namespace(prompt="force push to main", project=None)
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out

        # Fielded match evidence
        assert "trigger_idf_sum=" in out
        assert "trigger_coverage=" in out
        assert "matched_trigger=" in out

    def test_output_contains_eligibility(self, cfg, db_with_rules, capsys):
        from nokori.commands.test import run

        args = argparse.Namespace(prompt="force push to main", project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        assert "eligibility:" in out
        # Decisions are: COLD, WARM, HOT, GATE
        assert "COLD" in out or "WARM" in out or "HOT" in out or "GATE" in out

    def test_output_contains_ranking_utility(self, cfg, db_with_rules, capsys):
        from nokori.commands.test import run

        args = argparse.Namespace(prompt="force push to main", project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        assert "ranking_utility=" in out

    def test_output_contains_state_and_scores(self, cfg, db_with_rules, capsys):
        from nokori.commands.test import run

        args = argparse.Namespace(prompt="force push to main", project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        # State indicators
        assert "state=" in out
        assert "severity=" in out
        # Score indicators
        assert "usefulness=" in out
        assert "fp=" in out
        assert "harmful=" in out

    def test_output_contains_bm25_and_rrf_scores(self, cfg, db_with_rules, capsys):
        from nokori.commands.test import run

        args = argparse.Namespace(prompt="force push to main", project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        assert "bm25=" in out
        assert "rrf=" in out

    def test_output_pool_and_embed_info(self, cfg, db_with_rules, capsys):
        from nokori.commands.test import run

        args = argparse.Namespace(prompt="force push to main", project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        assert "formal.pool" in out
        assert "embed.mode" in out
        assert "bm25.matches" in out


# ---------------------------------------------------------------------------
# 2. CLI show command: structured fields, versions, origin, scores, lifecycle
# ---------------------------------------------------------------------------


class TestCliShowCommand:
    def test_show_displays_identity_and_versions(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="abc1")
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id              rule-1" in out
        assert "short_id        abc1" in out
        assert "schema_version         1" in out
        assert "rule_version           2" in out
        assert "runtime_policy_version policy-v2" in out
        assert "created_by_pipeline    pipeline-v3" in out

    def test_show_displays_source_and_activation_origin(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="ghi3")
        run(args, cfg)
        out = capsys.readouterr().out
        assert "source_origin   transcript_extraction" in out
        assert "activation_origin cold_fast_lane" in out

    def test_show_displays_structured_fields(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="abc1")
        run(args, cfg)
        out = capsys.readouterr().out
        assert "concepts:" in out
        assert "required_concept_groups:" in out
        assert "excluded_contexts:" in out

    def test_show_displays_scores(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="abc1")
        run(args, cfg)
        out = capsys.readouterr().out
        assert "scores:" in out
        assert "quality" in out
        assert "evidence_support" in out
        assert "specificity" in out
        assert "retrieval_readiness" in out
        assert "observed_usefulness" in out
        assert "plausible_usefulness" in out
        assert "false_positive" in out
        assert "harmful" in out

    def test_show_displays_lifecycle_timestamps(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="ghi3")
        run(args, cfg)
        out = capsys.readouterr().out
        assert "lifecycle:" in out
        assert "first_observed_useful_at 2026-05-10T00:00:00Z" in out
        assert "trusted_at               2026-05-15T00:00:00Z" in out
        assert "created_at" in out
        assert "updated_at" in out


# ---------------------------------------------------------------------------
# 3. CLI list: uses new statuses only (no merged/dormant)
# ---------------------------------------------------------------------------


class TestCliListCommand:
    def test_list_uses_valid_statuses(self, cfg, db_with_rules, capsys):
        from nokori.commands.list_rules import run

        args = argparse.Namespace(all=True, project=None)
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        # Should see active, trusted, archived (the rules we inserted)
        assert "active" in out
        assert "trusted" in out
        assert "archived" in out
        # Must NOT reference deprecated merged/dormant statuses
        assert "merged" not in out
        assert "dormant" not in out

    def test_list_default_filter_excludes_archived(self, cfg, db_with_rules, capsys):
        from nokori.commands.list_rules import run

        # Default (non --all) shows active, trusted, candidate only
        args = argparse.Namespace(all=False, project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        # active and trusted rules visible
        assert "abc1" in out
        assert "ghi3" in out
        # archived rule not shown in default view
        lines = out.strip().split("\n")
        short_ids_in_output = [
            line.split()[0]
            for line in lines
            if line.strip() and not line.startswith(" ")
        ]
        assert "def2" not in short_ids_in_output

    def test_list_shows_scores(self, cfg, db_with_rules, capsys):
        from nokori.commands.list_rules import run

        args = argparse.Namespace(all=True, project=None)
        run(args, cfg)
        out = capsys.readouterr().out
        assert "useful=" in out
        assert "fp=" in out
        assert "harmful=" in out


# ---------------------------------------------------------------------------
# 4. CLI status: pending job counts and pool stats
# ---------------------------------------------------------------------------


class TestCliStatusCommand:
    def test_status_shows_pending_jobs(self, cfg, db_with_rules, capsys, monkeypatch):
        from nokori.commands.status import run

        # Add pending jobs to DB
        db = db_with_rules
        with db.transaction() as tx:
            for i in range(3):
                tx.execute(
                    "INSERT INTO llm_jobs (id, role, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (f"job-{i}", "cold_reviewer", "pending", NOW, NOW),
                )
            # posthoc_jobs.fire_event_id references rule_fire_events
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, level, created_at) "
                "VALUES (?,?,?,?,?)",
                ("fe-1", "rule-1", "sess-1", "hot", NOW),
            )
            tx.execute(
                "INSERT INTO posthoc_jobs (id, fire_event_id, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                ("pj-1", "fe-1", "pending", NOW, NOW),
            )

        # Mock install hooks and embed server to avoid filesystem dependencies
        monkeypatch.setattr(
            "nokori.commands.status.describe_claude_hooks",
            lambda: {"installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.describe_cursor_hooks",
            lambda: {"installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.describe_dual_hook_registration",
            lambda: {"both_installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.coalesce_enabled",
            lambda: False,
        )
        monkeypatch.setattr(
            "nokori.commands.status.embed_ipc.server_status",
            lambda cfg: {"running": False, "pid": None, "idle_seconds": 0},
        )
        monkeypatch.setattr(
            "nokori.commands.status.job_io.list_jobs",
            lambda cfg, status=None: [],
        )
        monkeypatch.setattr(
            "nokori.commands.status.sessions.list_session_records",
            lambda cfg: [],
        )
        monkeypatch.setattr(
            "nokori.commands.status.sessions.list_active_sessions",
            lambda cfg, records=None: [],
        )

        args = argparse.Namespace()
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out

        assert "cold.pending   3" in out
        assert "posthoc.pending 1" in out

    def test_status_shows_idf_pool_stats(self, cfg, db_with_rules, capsys, monkeypatch):
        from nokori.commands.status import run

        monkeypatch.setattr(
            "nokori.commands.status.describe_claude_hooks",
            lambda: {"installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.describe_cursor_hooks",
            lambda: {"installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.describe_dual_hook_registration",
            lambda: {"both_installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.coalesce_enabled",
            lambda: False,
        )
        monkeypatch.setattr(
            "nokori.commands.status.embed_ipc.server_status",
            lambda cfg: {"running": False, "pid": None, "idle_seconds": 0},
        )
        monkeypatch.setattr(
            "nokori.commands.status.job_io.list_jobs",
            lambda cfg, status=None: [],
        )
        monkeypatch.setattr(
            "nokori.commands.status.sessions.list_session_records",
            lambda cfg: [],
        )
        monkeypatch.setattr(
            "nokori.commands.status.sessions.list_active_sessions",
            lambda cfg, records=None: [],
        )

        args = argparse.Namespace()
        run(args, cfg)
        out = capsys.readouterr().out

        assert "idf.pool_size" in out
        assert "idf.pool_version" in out
        assert "idf.dynamic_threshold" in out
        assert "idf.unique_tokens" in out

    def test_status_shows_circuit_breaker_info(self, cfg, db_with_rules, capsys, monkeypatch):
        from nokori.commands.status import run

        monkeypatch.setattr(
            "nokori.commands.status.describe_claude_hooks",
            lambda: {"installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.describe_cursor_hooks",
            lambda: {"installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.describe_dual_hook_registration",
            lambda: {"both_installed": False},
        )
        monkeypatch.setattr(
            "nokori.commands.status.coalesce_enabled",
            lambda: False,
        )
        monkeypatch.setattr(
            "nokori.commands.status.embed_ipc.server_status",
            lambda cfg: {"running": False, "pid": None, "idle_seconds": 0},
        )
        monkeypatch.setattr(
            "nokori.commands.status.job_io.list_jobs",
            lambda cfg, status=None: [],
        )
        monkeypatch.setattr(
            "nokori.commands.status.sessions.list_session_records",
            lambda cfg: [],
        )
        monkeypatch.setattr(
            "nokori.commands.status.sessions.list_active_sessions",
            lambda cfg, records=None: [],
        )

        args = argparse.Namespace()
        run(args, cfg)
        out = capsys.readouterr().out

        assert "circuit_breakers.open" in out


# ---------------------------------------------------------------------------
# 5. Web retrieve API: decision features and penalties
# ---------------------------------------------------------------------------


class TestWebRetrieveApi:
    def test_retrieve_returns_decision_features(self, client):
        resp = client.post(
            "/api/retrieve",
            json={"prompt": "force push to main", "project_id": None},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "hot" in data
        assert "warm" in data
        assert "embed_mode" in data
        assert "bm25_matches" in data

        # If any rules matched, they must have decision_features
        for item in data["hot"] + data["warm"]:
            assert "decision_features" in item
            df = item["decision_features"]
            assert "trigger_idf_sum" in df
            assert "trigger_coverage" in df
            assert "distinct_trigger_terms" in df
            assert "strong_variant_phrase_hit" in df
            assert "required_concepts_match" in df
            assert "excluded_context_hit" in df
            assert "action_only_match" in df
            assert "search_only_match" in df
            assert "embedding_only_match" in df
            assert "matched_trigger_tokens" in df

    def test_retrieve_returns_eligibility(self, client):
        resp = client.post(
            "/api/retrieve",
            json={"prompt": "force push to main"},
        )
        data = resp.json()["data"]
        for item in data["hot"] + data["warm"]:
            assert "eligibility" in item
            elig = item["eligibility"]
            assert "decision" in elig
            assert "eligible" in elig
            assert "reason" in elig

    def test_retrieve_returns_ranking_utility(self, client):
        resp = client.post(
            "/api/retrieve",
            json={"prompt": "force push to main"},
        )
        data = resp.json()["data"]
        for item in data["hot"] + data["warm"]:
            assert "ranking_utility" in item
            assert "rrf_score" in item
            assert "bm25_score" in item

    def test_retrieve_empty_prompt(self, client):
        resp = client.post("/api/retrieve", json={"prompt": ""})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["hot"] == []
        assert data["warm"] == []


# ---------------------------------------------------------------------------
# 6. Web rules API: structured fields and scores
# ---------------------------------------------------------------------------


class TestWebRulesApi:
    def test_rules_list_returns_structured_fields(self, client):
        resp = client.get("/api/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] >= 1
        rule = body["data"][0]

        # Identity
        assert "id" in rule
        assert "short_id" in rule
        assert "schema_version" in rule
        assert "rule_version" in rule

        # Structured trigger fields
        assert "trigger_canonical" in rule
        assert "concepts" in rule
        assert "required_concept_groups" in rule
        assert "excluded_contexts" in rule
        assert "trigger_variants" in rule
        assert "search_terms" in rule

        # Action fields
        assert "action_instruction" in rule
        assert "allowed_behavior" in rule
        assert "forbidden_behavior" in rule

        # Scores
        assert "quality_score" in rule
        assert "evidence_support_score" in rule
        assert "specificity_score" in rule
        assert "retrieval_readiness_score" in rule
        assert "observed_usefulness_score" in rule
        assert "plausible_usefulness_score" in rule
        assert "false_positive_score" in rule
        assert "harmful_score" in rule

        # Origin
        assert "source_origin" in rule
        assert "activation_origin" in rule

    def test_rules_detail_returns_all_fields(self, client):
        resp = client.get("/api/rules/abc1")
        assert resp.status_code == 200
        rule = resp.json()["data"]
        assert rule["short_id"] == "abc1"
        assert rule["status"] == "active"
        assert rule["severity"] == "reminder"
        assert rule["quality_score"] == 0.85
        assert rule["source_origin"] == "transcript_extraction"
        assert rule["created_by_pipeline_version"] == "pipeline-v3"
        assert rule["runtime_policy_version"] == "policy-v2"

    def test_rules_detail_includes_lifecycle_timestamps(self, client):
        resp = client.get("/api/rules/ghi3")
        rule = resp.json()["data"]
        assert rule["trusted_at"] == "2026-05-15T00:00:00Z"
        assert rule["first_observed_useful_at"] == "2026-05-10T00:00:00Z"
        assert rule["created_at"] == NOW
        assert rule["updated_at"] == NOW


# ---------------------------------------------------------------------------
# 7. CLI/web expose archive/dismiss ONLY
# ---------------------------------------------------------------------------


class TestArchiveDismissExposed:
    def _authorize(self, client):
        client.get("/api/config")

    def test_web_archive_endpoint_works(self, client):
        self._authorize(client)
        resp = client.post("/api/rules/abc1/archive")
        assert resp.status_code == 200
        rule = resp.json()["data"]
        assert rule["status"] == "archived"

    def test_web_dismiss_endpoint_works(self, client):
        self._authorize(client)
        # Use ghi3 (trusted) for dismiss test
        resp = client.post("/api/rules/ghi3/dismiss")
        assert resp.status_code == 200
        rule = resp.json()["data"]
        assert rule["status"] == "archived"

    def test_web_archive_404_for_unknown(self, client):
        self._authorize(client)
        resp = client.post("/api/rules/zzz999/archive")
        assert resp.status_code == 404

    def test_web_dismiss_404_for_unknown(self, client):
        self._authorize(client)
        resp = client.post("/api/rules/zzz999/dismiss")
        assert resp.status_code == 404

    def test_web_archive_idempotent_on_already_archived(self, client):
        self._authorize(client)
        # def2 is already archived
        resp = client.post("/api/rules/def2/archive")
        assert resp.status_code == 200
        rule = resp.json()["data"]
        assert rule["status"] == "archived"


# ---------------------------------------------------------------------------
# 8. CLI/web do NOT expose manual promote/trust/suppress
# ---------------------------------------------------------------------------


class TestManualLifecycleRejected:
    def test_web_promote_rejected(self, client):
        client.get("/api/config")
        resp = client.post("/api/rules/abc1/promote")
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "Manual promote is not supported" in detail
        assert "flywheel" in detail.lower() or "autonomously" in detail.lower()

    def test_web_trust_rejected(self, client):
        client.get("/api/config")
        resp = client.post("/api/rules/abc1/trust")
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "Manual trust is not supported" in detail

    def test_web_suppress_rejected(self, client):
        client.get("/api/config")
        resp = client.post("/api/rules/abc1/suppress")
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "Manual suppress is not supported" in detail

    def test_rejected_endpoints_do_not_mutate(self, client):
        """Rejected manual lifecycle calls must NOT change rule status."""
        client.get("/api/config")
        # Get current state
        before = client.get("/api/rules/abc1").json()["data"]
        assert before["status"] == "active"

        # Attempt all rejected operations
        client.post("/api/rules/abc1/promote")
        client.post("/api/rules/abc1/trust")
        client.post("/api/rules/abc1/suppress")

        # Status unchanged
        after = client.get("/api/rules/abc1").json()["data"]
        assert after["status"] == "active"


# ---------------------------------------------------------------------------
# 9. Rule detail includes archived fingerprint summary when archived
# ---------------------------------------------------------------------------


class TestArchivedFingerprintSummary:
    def test_show_archived_rule_includes_fingerprint(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="def2")
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "archived:" in out
        assert "reason:" in out
        assert "superseded_by_merge" in out
        assert "replacement_id:" in out
        assert "rule-1" in out

    def test_show_active_rule_no_archived_section(self, cfg, db_with_rules, capsys):
        from nokori.commands.show import run

        args = argparse.Namespace(short_id="abc1")
        run(args, cfg)
        out = capsys.readouterr().out
        # Active rule should NOT have the archived section
        assert "archived:" not in out

    def test_web_archived_rule_has_reason_and_replacement(self, client):
        resp = client.get("/api/rules/def2")
        assert resp.status_code == 200
        rule = resp.json()["data"]
        assert rule["status"] == "archived"
        assert rule["archived_reason"] == "superseded_by_merge"
        assert rule["replacement_id"] == "rule-1"

    def test_web_active_rule_has_null_archive_fields(self, client):
        resp = client.get("/api/rules/abc1")
        rule = resp.json()["data"]
        assert rule["archived_reason"] is None
        assert rule["replacement_id"] is None
