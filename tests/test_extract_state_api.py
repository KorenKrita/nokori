"""Integration tests for GET /api/extract/state endpoint."""
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("httpx")

from dataclasses import replace

from fastapi.testclient import TestClient

from nokori.config import Config
from nokori.db import open_db
from nokori.utils.time import now_iso
from nokori.web.app import create_app


@pytest.fixture
def cfg(tmp_path):
    base = Config.from_env()
    return replace(base, data_dir=tmp_path)


@pytest.fixture
def db(cfg):
    database = open_db(cfg.db_path)
    yield database
    database.close()


@pytest.fixture
def client(cfg):
    app = create_app(cfg)
    return TestClient(app)


def _insert_extract_state(db, transcript_path, *, status="done", offset=0):
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO extract_state "
            "(transcript_path, transcript_mtime, extracted_at, status, last_byte_offset) "
            "VALUES (?, ?, ?, ?, ?)",
            (transcript_path, 1000.0, now, status, offset),
        )


def _insert_rule(db, rule_id, *, transcript_ref, short_id=None):
    now = now_iso()
    sid = short_id or rule_id[:8]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, trigger_variants, "
            "search_terms, action_instruction, "
            "source_origin, status, severity, "
            "evidence_support_score, transcript_ref, "
            "project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id, sid, 1, 1,
                "v1", "v1",
                "test trigger",
                '[]',
                '{}',
                "test action",
                "transcript_extraction", "active", "reminder",
                3.0, transcript_ref,
                "global", now, now,
            ),
        )


def _insert_review(db, rule_id, *, role="evaluator", decision="approve"):
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_reviews (role, decision, scores, rule_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (role, decision, json.dumps({"quality": 0.8}), rule_id, now),
        )


def _insert_lineage(db, old_rule_id, new_rule_id, *, operation="merge"):
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_lineage (old_rule_id, new_rule_id, operation, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (old_rule_id, new_rule_id, operation, "test reason", now),
        )


def _insert_hook_event(db, transcript_ref, *, outcome="success"):
    now = now_iso()
    event_id = uuid.uuid4().hex
    details = json.dumps({"transcript_ref": transcript_ref})
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO hook_events (id, source, outcome, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, "cold_pipeline", outcome, details, now),
        )


class TestExtractStateEmpty:
    def test_empty_state(self, client):
        """No transcripts seeded -- returns empty list."""
        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestExtractStateSingleTranscript:
    def test_single_transcript_with_rules(self, db, client):
        """Seed extract_state + rules with matching transcript_ref, verify rules grouped under transcript."""
        _insert_extract_state(db, "/transcripts/session1.md")
        _insert_rule(db, "rule-a1", transcript_ref="/transcripts/session1.md", short_id="aaa")
        _insert_rule(db, "rule-a2", transcript_ref="/transcripts/session1.md", short_id="bbb")

        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        entry = data[0]
        assert entry["transcript_path"] == "/transcripts/session1.md"
        assert entry["status"] == "done"
        assert len(entry["rules"]) == 2
        rule_ids = {r["id"] for r in entry["rules"]}
        assert rule_ids == {"rule-a1", "rule-a2"}
        # Each rule should have empty reviews and lineage by default
        for rule in entry["rules"]:
            assert rule["reviews"] == []
            assert rule["lineage"] == []


class TestExtractStateReviewsAndLineage:
    def test_reviews_and_lineage_joins(self, db, client):
        """Seed rule_reviews and rule_lineage, verify they appear correctly nested."""
        _insert_extract_state(db, "/transcripts/s2.md")
        _insert_rule(db, "rule-b1", transcript_ref="/transcripts/s2.md", short_id="b1s")
        _insert_rule(db, "rule-b2", transcript_ref="/transcripts/s2.md", short_id="b2s")
        _insert_review(db, "rule-b1", role="evaluator", decision="approve")
        _insert_review(db, "rule-b1", role="critic", decision="reject")
        _insert_lineage(db, "rule-b1", "rule-b2", operation="merge")

        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        rules = {r["id"]: r for r in data[0]["rules"]}

        # rule-b1 has 2 reviews
        assert len(rules["rule-b1"]["reviews"]) == 2
        roles = {rv["role"] for rv in rules["rule-b1"]["reviews"]}
        assert roles == {"evaluator", "critic"}
        # Verify scores are parsed JSON, not string
        for rv in rules["rule-b1"]["reviews"]:
            assert isinstance(rv["scores"], dict)

        # Lineage: rule-b1 appears as old_rule_id, rule-b2 as new_rule_id
        # Both should have the lineage entry since endpoint adds to both sides
        assert len(rules["rule-b1"]["lineage"]) == 1
        assert len(rules["rule-b2"]["lineage"]) == 1
        ln = rules["rule-b1"]["lineage"][0]
        assert ln["old_rule_id"] == "rule-b1"
        assert ln["new_rule_id"] == "rule-b2"
        assert ln["operation"] == "merge"
        assert ln["reason"] == "test reason"


class TestExtractStatePagination:
    def test_pagination_boundary(self, db, client):
        """Seed >100 extract_state records, verify only 100 returned."""
        for i in range(105):
            _insert_extract_state(db, f"/transcripts/t{i:04d}.md")

        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 100


class TestExtractStateBatchChunking:
    def test_batch_chunking(self, db, client):
        """Seed >450 rules, verify all rules and their reviews are returned.

        The endpoint batches rule_ids in chunks of 450 for reviews/lineage queries.
        This test ensures all reviews are correctly associated regardless of which
        internal batch a rule falls into (endpoint ordering may differ from insertion
        order). We seed 500 rules across 2 transcripts (within the LIMIT 500) and
        attach a review to an arbitrary rule, then verify it appears in the response.
        """
        _insert_extract_state(db, "/transcripts/big1.md")
        _insert_extract_state(db, "/transcripts/big2.md")
        rule_ids = []
        for i in range(250):
            rid = f"rule-big1-{i:04d}"
            _insert_rule(db, rid, transcript_ref="/transcripts/big1.md", short_id=f"s1{i:04d}")
            rule_ids.append(rid)
        for i in range(250):
            rid = f"rule-big2-{i:04d}"
            _insert_rule(db, rid, transcript_ref="/transcripts/big2.md", short_id=f"s2{i:04d}")
            rule_ids.append(rid)
        # Add a review for a rule in the second batch (index > 450)
        late_rule = rule_ids[460]
        _insert_review(db, late_rule, role="critic", decision="reject")

        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        data = resp.json()["data"]

        # Collect all rules from both transcripts
        all_returned_rules = []
        for entry in data:
            all_returned_rules.extend(entry["rules"])
        assert len(all_returned_rules) == 500

        # Verify the review on the late-batch rule is present
        late_rule_data = next(r for r in all_returned_rules if r["id"] == late_rule)
        assert len(late_rule_data["reviews"]) == 1
        assert late_rule_data["reviews"][0]["role"] == "critic"


class TestExtractStatePipelineEvents:
    def test_pipeline_events_included(self, db, client):
        """Verify hook_events with source=cold_pipeline appear in pipeline_events."""
        _insert_extract_state(db, "/transcripts/ev.md")
        _insert_hook_event(db, "/transcripts/ev.md", outcome="success")
        _insert_hook_event(db, "/transcripts/ev.md", outcome="error")

        resp = client.get("/api/extract/state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        events = data[0]["pipeline_events"]
        assert len(events) == 2
        outcomes = {e["outcome"] for e in events}
        assert outcomes == {"success", "error"}
        # Verify details is parsed dict, not string
        for ev in events:
            assert isinstance(ev["details"], dict)
            assert ev["details"]["transcript_ref"] == "/transcripts/ev.md"
