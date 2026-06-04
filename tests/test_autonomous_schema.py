"""Autonomous Rule Quality Flywheel schema tests (v6 redesign).

Tests that the new DDL enforces constraints, stores versioning fields,
and that open_db / policy constants behave correctly.
"""
import sqlite3
import uuid
from pathlib import Path

import pytest

from nokori.db import SCHEMA_VERSION, open_db
from nokori.errors import DbError
from nokori import policy


@pytest.fixture()
def db(tmp_path):
    d = open_db(tmp_path / "rules.db")
    yield d
    d.close()


def _now() -> str:
    return "2026-06-04T00:00:00Z"


def _insert_rule(conn, *, status="candidate", rule_id=None, short_id=None, **overrides):
    """Insert a minimal valid rule row, returning its id."""
    rid = rule_id or str(uuid.uuid4())
    sid = short_id or rid[:8]
    defaults = {
        "id": rid,
        "short_id": sid,
        "schema_version": 1,
        "rule_version": 1,
        "runtime_policy_version": "pol-1",
        "status": status,
        "severity": "reminder",
        "trigger_canonical": "do the thing",
        "action_instruction": "fix it",
        "source_origin": "transcript_extraction",
        "project_scope": "project",
        "created_at": _now(),
        "updated_at": _now(),
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(
        f"INSERT INTO rules ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    return rid


# ---------------------------------------------------------------------------
# 1. rules.status CHECK constraint
# ---------------------------------------------------------------------------


class TestRulesStatusConstraint:
    @pytest.mark.parametrize("valid_status", [
        "candidate", "active", "trusted", "suppressed", "archived",
    ])
    def test_valid_statuses_accepted(self, db, valid_status):
        with db.transaction() as conn:
            _insert_rule(conn, status=valid_status)

    @pytest.mark.parametrize("invalid_status", ["merged", "dormant", "draft", ""])
    def test_invalid_statuses_rejected(self, db, invalid_status):
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                _insert_rule(conn, status=invalid_status)


# ---------------------------------------------------------------------------
# 2. rules store versioning and origin fields
# ---------------------------------------------------------------------------


class TestRulesVersioningFields:
    def test_stores_schema_version(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn, schema_version=3)
        row = db.fetchone("SELECT schema_version FROM rules WHERE id = ?", (rid,))
        assert row["schema_version"] == 3

    def test_stores_rule_version(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn, rule_version=7)
        row = db.fetchone("SELECT rule_version FROM rules WHERE id = ?", (rid,))
        assert row["rule_version"] == 7

    def test_stores_runtime_policy_version(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn, runtime_policy_version="pol-2.1")
        row = db.fetchone("SELECT runtime_policy_version FROM rules WHERE id = ?", (rid,))
        assert row["runtime_policy_version"] == "pol-2.1"

    def test_stores_source_origin(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn, source_origin="external_source_material")
        row = db.fetchone("SELECT source_origin FROM rules WHERE id = ?", (rid,))
        assert row["source_origin"] == "external_source_material"

    def test_invalid_source_origin_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                _insert_rule(conn, source_origin="invented")

    def test_stores_activation_origin(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn, activation_origin="cold_fast_lane")
        row = db.fetchone("SELECT activation_origin FROM rules WHERE id = ?", (rid,))
        assert row["activation_origin"] == "cold_fast_lane"

    def test_stores_first_observed_useful_at(self, db):
        ts = "2026-05-30T12:00:00Z"
        with db.transaction() as conn:
            rid = _insert_rule(conn, first_observed_useful_at=ts)
        row = db.fetchone("SELECT first_observed_useful_at FROM rules WHERE id = ?", (rid,))
        assert row["first_observed_useful_at"] == ts


# ---------------------------------------------------------------------------
# 3. rule_fire_events stores injected snapshots and version fields
# ---------------------------------------------------------------------------


class TestRuleFireEvents:
    def test_stores_injected_snapshots_and_versions(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn)
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO rule_fire_events "
                "(id, rule_id, session_id, injected_rule_version, "
                " injected_trigger_snapshot, injected_action_snapshot, "
                " injected_structured_snapshot, trigger_idf_pool_version, "
                " runtime_policy_version, embedding_profile_version, "
                " prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    eid, rid, "sess-1", 5,
                    "trigger snap", "action snap",
                    '{"key":"val"}', "idf-pool-3",
                    "pol-2", "emb-prof-1",
                    "abc123", "hot", _now(),
                ),
            )

        row = db.fetchone("SELECT * FROM rule_fire_events WHERE id = ?", (eid,))
        assert row["injected_rule_version"] == 5
        assert row["injected_trigger_snapshot"] == "trigger snap"
        assert row["injected_action_snapshot"] == "action snap"
        assert row["injected_structured_snapshot"] == '{"key":"val"}'
        assert row["trigger_idf_pool_version"] == "idf-pool-3"
        assert row["runtime_policy_version"] == "pol-2"
        assert row["embedding_profile_version"] == "emb-prof-1"

    def test_level_check_constraint(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn)
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO rule_fire_events (id, rule_id, level, created_at) "
                    "VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), rid, "invalid_level", _now()),
                )


# ---------------------------------------------------------------------------
# 4. rule_shadow_events stores shadow snapshots and version fields
# ---------------------------------------------------------------------------


class TestRuleShadowEvents:
    def test_stores_shadow_snapshots_and_versions(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn)
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO rule_shadow_events "
                "(id, rule_id, session_id, shadow_rule_version, "
                " shadow_trigger_snapshot, shadow_action_snapshot, "
                " shadow_structured_snapshot, status_at_match, shadow_type, "
                " trigger_idf_pool_version, runtime_policy_version, "
                " embedding_profile_version, context_fingerprint, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    eid, rid, "sess-2", 3,
                    "shadow trigger", "shadow action",
                    '{"s":"snap"}', "candidate", "candidate_probe",
                    "idf-pool-2", "pol-1",
                    "emb-prof-2", "fp-abc123", _now(),
                ),
            )

        row = db.fetchone("SELECT * FROM rule_shadow_events WHERE id = ?", (eid,))
        assert row["shadow_rule_version"] == 3
        assert row["shadow_trigger_snapshot"] == "shadow trigger"
        assert row["shadow_action_snapshot"] == "shadow action"
        assert row["shadow_structured_snapshot"] == '{"s":"snap"}'
        assert row["context_fingerprint"] == "fp-abc123"
        assert row["trigger_idf_pool_version"] == "idf-pool-2"
        assert row["runtime_policy_version"] == "pol-1"
        assert row["embedding_profile_version"] == "emb-prof-2"

    def test_status_at_match_check(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn)
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO rule_shadow_events "
                    "(id, rule_id, status_at_match, created_at) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), rid, "active", _now()),
                )

    def test_shadow_type_check(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn)
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO rule_shadow_events "
                    "(id, rule_id, shadow_type, created_at) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), rid, "invalid_type", _now()),
                )


# ---------------------------------------------------------------------------
# 5. rule_synthetic_evals stores exact version columns
# ---------------------------------------------------------------------------


class TestRuleSyntheticEvals:
    def test_stores_all_version_fields(self, db):
        with db.transaction() as conn:
            rid = _insert_rule(conn)
            conn.execute(
                "INSERT INTO rule_synthetic_evals "
                "(rule_id, rule_version, runtime_policy_version, "
                " tokenizer_version, matcher_compiler_version, "
                " concept_compiler_version, embedding_profile_version, "
                " trigger_idf_pool_version, benchmark_version, "
                " passed, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid, 4, "pol-3",
                    "tok-2.0", "mc-1.5",
                    "cc-1.0", "emb-3",
                    "idf-pool-7", "bench-2",
                    1, _now(),
                ),
            )

        row = db.fetchone(
            "SELECT * FROM rule_synthetic_evals WHERE rule_id = ?", (rid,)
        )
        assert row["rule_version"] == 4
        assert row["runtime_policy_version"] == "pol-3"
        assert row["tokenizer_version"] == "tok-2.0"
        assert row["matcher_compiler_version"] == "mc-1.5"
        assert row["concept_compiler_version"] == "cc-1.0"
        assert row["embedding_profile_version"] == "emb-3"
        assert row["trigger_idf_pool_version"] == "idf-pool-7"
        assert row["benchmark_version"] == "bench-2"


# ---------------------------------------------------------------------------
# 6. trigger_idf_stats stores version columns
# ---------------------------------------------------------------------------


class TestTriggerIdfStats:
    def test_stores_version_fields(self, db):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO trigger_idf_stats "
                "(pool_version, rule_pool_size, eligible_rule_set_hash, "
                " tokenizer_version, matcher_compiler_version, "
                " generic_token_policy_version, concept_compiler_version, "
                " df_by_token, dynamic_threshold, built_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "pool-v5", 120, "hash-abc",
                    "tok-2.1", "mc-2.0",
                    "gtp-1.0", "cc-1.1",
                    '{"term":3}', 1.5, _now(),
                ),
            )

        row = db.fetchone(
            "SELECT * FROM trigger_idf_stats WHERE pool_version = ?", ("pool-v5",)
        )
        assert row["tokenizer_version"] == "tok-2.1"
        assert row["matcher_compiler_version"] == "mc-2.0"
        assert row["generic_token_policy_version"] == "gtp-1.0"
        assert row["concept_compiler_version"] == "cc-1.1"
        assert row["eligible_rule_set_hash"] == "hash-abc"


# ---------------------------------------------------------------------------
# 7. open_db with fresh path creates all tables
# ---------------------------------------------------------------------------


class TestOpenDbFreshPath:
    def test_creates_all_tables(self, tmp_path):
        db = open_db(tmp_path / "fresh.db")
        try:
            tables = {
                r["name"]
                for r in db.fetchall(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            expected = {
                "rules",
                "rule_embeddings",
                "rule_reviews",
                "rule_synthetic_evals",
                "embedding_benchmark_profiles",
                "trigger_idf_stats",
                "rule_fire_events",
                "rule_shadow_events",
                "rule_feedback_events",
                "rule_lineage",
                "archived_fingerprints",
                "llm_jobs",
                "transcript_ingest_jobs",
                "posthoc_jobs",
                "extract_state",
                "maintenance_meta",
            }
            assert expected.issubset(tables)
            assert db.schema_version() == SCHEMA_VERSION == 6
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 8. open_db with old schema version raises DbError
# ---------------------------------------------------------------------------


class TestOpenDbOldSchema:
    def test_schema_version_5_raises(self, tmp_path):
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            "CREATE TABLE rules (id TEXT PRIMARY KEY);\n"
            "PRAGMA user_version = 5;\n"
        )
        conn.close()
        with pytest.raises(DbError, match="incompatible"):
            open_db(db_path)

    def test_schema_version_1_raises(self, tmp_path):
        db_path = tmp_path / "v1.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            "CREATE TABLE rules (id TEXT PRIMARY KEY);\n"
            "PRAGMA user_version = 1;\n"
        )
        conn.close()
        with pytest.raises(DbError, match="incompatible"):
            open_db(db_path)

    def test_newer_schema_raises(self, tmp_path):
        db_path = tmp_path / "future.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            "CREATE TABLE rules (id TEXT PRIMARY KEY);\n"
            "PRAGMA user_version = 99;\n"
        )
        conn.close()
        with pytest.raises(DbError, match="newer nokori"):
            open_db(db_path)


# ---------------------------------------------------------------------------
# 9. Policy constants accessible and have expected types
# ---------------------------------------------------------------------------


class TestPolicyConstants:
    def test_cold_fast_lane_thresholds(self):
        assert isinstance(policy.COLD_FAST_LANE, policy.ColdFastLaneThresholds)
        assert isinstance(policy.COLD_FAST_LANE.admission_overall_quality_min, float)
        assert policy.COLD_FAST_LANE.admission_overall_quality_min == 0.90

    def test_candidate_to_active_thresholds(self):
        t = policy.CANDIDATE_TO_ACTIVE
        assert isinstance(t, policy.CandidateToActiveThresholds)
        assert isinstance(t.shadow_strong_match_count_min, int)
        assert t.shadow_strong_match_count_min == 3

    def test_active_to_trusted_thresholds(self):
        t = policy.ACTIVE_TO_TRUSTED
        assert isinstance(t, policy.ActiveToTrustedThresholds)
        assert isinstance(t.observed_useful_count_min, int)
        assert isinstance(t.recent_false_positive_rate_max, float)

    def test_suppressed_to_active_thresholds(self):
        t = policy.SUPPRESSED_TO_ACTIVE
        assert isinstance(t, policy.SuppressedToActiveThresholds)
        assert isinstance(t.shadow_recovery_would_help_high_min, int)

    def test_runtime_constants(self):
        assert isinstance(policy.WARM_HARD_MAX, int)
        assert isinstance(policy.HOT_MAX_DEFAULT, int)
        assert isinstance(policy.RECENT_EVENT_WINDOW, int)
        assert isinstance(policy.SHADOW_EVENT_WINDOW, int)

    def test_cas_fields_tuple(self):
        assert isinstance(policy.CAS_FIELDS, tuple)
        assert "rule_version" in policy.CAS_FIELDS
        assert "runtime_policy_version" in policy.CAS_FIELDS

    def test_dynamic_idf_policies(self):
        assert isinstance(policy.DYNAMIC_IDF_SMALL_POOL, policy.DynamicIDFPolicy)
        assert isinstance(policy.DYNAMIC_IDF_NORMAL, policy.DynamicIDFPolicy)
        assert policy.DYNAMIC_IDF_SMALL_POOL.absolute_trigger_info_min > policy.DYNAMIC_IDF_NORMAL.absolute_trigger_info_min

    def test_false_positive_reason_codes(self):
        assert isinstance(policy.FALSE_POSITIVE_REASON_CODES, frozenset)
        assert "harmful_wrong_scope" in policy.FALSE_POSITIVE_REASON_CODES

    def test_evaluated_labels(self):
        assert isinstance(policy.EVALUATED_LABELS, frozenset)
        assert "observed_useful" in policy.EVALUATED_LABELS
        assert "unclear" not in policy.EVALUATED_LABELS
