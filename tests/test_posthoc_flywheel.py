"""Tests for the posthoc evaluation flywheel (jobs + evaluator)."""

import json
import uuid

import pytest

from nokori.db import open_db
from nokori.events.fire import create_fire_event
from nokori.gate.marker import prompt_hash
from nokori.models import Rule
from nokori.posthoc.evaluator import (
    ATTRIBUTION_ANSWERS,
    POSTHOC_LABELS,
    compute_attribution_weight,
    parse_posthoc_output,
)
from nokori.posthoc.jobs import (
    build_evaluator_input,
    enqueue_posthoc_for_session,
    get_pending_posthoc_jobs,
    mark_posthoc_job_complete,
    mark_posthoc_job_unclear,
    process_pending_posthoc_jobs,
)
from nokori.utils.prompt_text import normalize_prompt_for_hash
from nokori.utils.time import now_iso


def _make_db(tmp_path):
    return open_db(tmp_path / "rules.db")


@pytest.fixture(autouse=True)
def _allow_tmp_transcript_roots(tmp_path, monkeypatch):
    """Permit transcript paths under tmp_path so build_evaluator_input can
    re-read real transcript files (is_path_allowed checks _allowed_roots).

    Previously fire events stored the raw user_prompt_submit prompt text in
    bounded_window_ref and posthoc used it directly as the window. That was a
    bug (see task 06-18-fix-posthoc-window-active-fire-loop): the prompt field
    frequently contains skill system prompts / task-notifications rather than
    the real conversation. Posthoc now re-reads the transcript file referenced
    by bounded_window_ref ("transcript:<path>") and locates the injection turn
    via prompt_hash. These tests construct real transcript files to exercise
    that path.
    """
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path],
    )


def _insert_rule(db, *, rule_id=None, status="active") -> Rule:
    rule_id = rule_id or str(uuid.uuid4())
    short_id = rule_id[:6]
    now = now_iso()
    concepts = json.dumps(["concept_a"])
    required_concept_groups = json.dumps(["group_1"])
    excluded_contexts = json.dumps(["excluded_ctx"])

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "status, severity, "
            "trigger_canonical, concepts, required_concept_groups, excluded_contexts, "
            "trigger_variants, "
            "action_instruction, "
            "domain_tags, tool_tags, path_patterns, "
            "source_origin, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id,
                short_id,
                1,
                1,
                "pipeline_v1",
                "policy_v1",
                status,
                "reminder",
                "trigger text here",
                concepts,
                required_concept_groups,
                excluded_contexts,
                json.dumps(["variant_a"]),
                "do the action",
                json.dumps(["domain_web"]),
                json.dumps(["tool_git"]),
                json.dumps(["src/**"]),
                "transcript_extraction",
                "project",
                now,
                now,
            ),
        )

    return Rule(
        id=rule_id,
        short_id=short_id,
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="pipeline_v1",
        runtime_policy_version="policy_v1",
        last_rewritten_by_role=None,
        status=status,
        severity="reminder",
        trigger_canonical="trigger text here",
        concepts=concepts,
        required_concept_groups=required_concept_groups,
        excluded_contexts=excluded_contexts,
        near_miss_examples=[],
        trigger_variants=["variant_a"],
        action_instruction="do the action",
        domain_tags=["domain_web"],
        tool_tags=["tool_git"],
        path_patterns=["src/**"],
        quality_score=0.0,
        evidence_support_score=0.0,
        specificity_score=0.0,
        retrieval_readiness_score=0.0,
        observed_usefulness_score=0.0,
        plausible_usefulness_score=0.0,
        false_positive_score=0.0,
        harmful_score=0.0,
        source_origin="transcript_extraction",
        activation_origin=None,
        first_observed_useful_at=None,
        trusted_at=None,
        suppressed_at=None,
        project_scope="project",
        project_id=None,
        archived_reason=None,
        replacement_id=None,
        created_at=now,
        updated_at=now,
    )


def _create_fire_event_with_window(db, rule, session_id, tmp_path, *, labeled=False):
    """Create a fire event backed by a real transcript file.

    Writes a JSONL transcript containing a user turn + assistant turn under
    tmp_path, then creates a fire event whose prompt_hash matches the user
    turn (so posthoc can locate the injection turn via prompt_hash) and whose
    bounded_window_ref is "transcript:<path>" (so posthoc re-reads the real
    transcript instead of using the polluted prompt text).

    This replaced the old pattern of storing the raw prompt text in
    bounded_window_ref — see task 06-18-fix-posthoc-window-active-fire-loop.
    """
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    # Unique filename per call so repeated invocations with the same session_id
    # don't overwrite each other's transcript (prompt_hash matching is
    # independent of the filename).
    transcript_path = transcript_dir / f"{session_id}_{uuid.uuid4().hex[:8]}.jsonl"

    user_prompt = (
        f"User asked about coding patterns in {session_id}. "
        "Assistant discussed best practices for error handling "
        "and provided examples of try-catch blocks."
    )
    normalized = normalize_prompt_for_hash(user_prompt)
    # Hash caliber mirrors prompt_inject.py.
    ph = prompt_hash(normalized or user_prompt)

    # Write a real transcript: user turn (injection point) + assistant turn.
    transcript_path.write_text(
        json.dumps({"role": "user", "content": user_prompt}) + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": "I'll explain error handling best practices with examples.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # turn_index=None (as real UserPromptSubmit payloads carry) so posthoc must
    # locate the injection turn via prompt_hash — this is the path the bugfix
    # relies on, and a hash-caliber regression would surface here.
    event_id = create_fire_event(
        db,
        rule,
        session_id,
        ph,
        "hot",
        {"score": 0.9},
        turn_index=None,
        bounded_window_ref=f"transcript:{transcript_path}",
    )
    if labeled:
        with db.transaction() as tx:
            tx.execute(
                "UPDATE rule_fire_events SET posthoc_label = ? WHERE id = ?",
                ("observed_useful", event_id),
            )
    return event_id


# ---------------------------------------------------------------------------
# 1. enqueue_posthoc_for_session creates jobs for unlabeled fire events only
# ---------------------------------------------------------------------------


class TestEnqueuePosthocForSession:
    def test_creates_jobs_for_unlabeled_only(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_1"

            # Two unlabeled events
            _create_fire_event_with_window(db, rule, session, tmp_path)
            _create_fire_event_with_window(db, rule, session, tmp_path)
            # One labeled event
            _create_fire_event_with_window(db, rule, session, tmp_path, labeled=True)

            count = enqueue_posthoc_for_session(db, session)
            assert count == 2

            jobs = get_pending_posthoc_jobs(db)
            assert len(jobs) == 2
        finally:
            db.close()

    def test_idempotent_enqueue(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_2"
            _create_fire_event_with_window(db, rule, session, tmp_path)

            first_count = enqueue_posthoc_for_session(db, session)
            second_count = enqueue_posthoc_for_session(db, session)

            assert first_count == 1
            assert second_count == 0
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 2. Each fire event gets a separate posthoc job
# ---------------------------------------------------------------------------


class TestSeparateJobsPerEvent:
    def test_one_job_per_fire_event(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_3"

            eid1 = _create_fire_event_with_window(db, rule, session, tmp_path)
            eid2 = _create_fire_event_with_window(db, rule, session, tmp_path)
            eid3 = _create_fire_event_with_window(db, rule, session, tmp_path)

            enqueue_posthoc_for_session(db, session)
            jobs = get_pending_posthoc_jobs(db)

            job_fire_ids = {j["fire_event_id"] for j in jobs}
            assert job_fire_ids == {eid1, eid2, eid3}
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 3. build_evaluator_input is partially blind (excludes status, scores, target)
# ---------------------------------------------------------------------------


class TestBuildEvaluatorInputPartiallyBlind:
    def test_excludes_status_scores_promotion(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_4"
            eid = _create_fire_event_with_window(db, rule, session, tmp_path)

            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (eid,)
            )
            event = dict(row)

            result = build_evaluator_input(db, event)
            assert result is not None

            # Must NOT contain rule status, scores, or promotion target
            assert "rule_status" not in result
            assert "status" not in result.get("decision_features", {})
            assert "quality_score" not in result.get("decision_features", {})

            # Verify excluded keys are not present
            assert "promotion_target" not in result
            assert "historical_scores" not in result
        finally:
            db.close()

    def test_returns_none_without_window_ref(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_5"
            # Create event then NULL out both window refs to simulate legacy/broken state
            eid = create_fire_event(
                db, rule, session, "hash_abc", "hot", {"score": 0.9}
            )
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rule_fire_events SET bounded_window_ref = NULL, "
                    "transcript_window_ref = NULL WHERE id = ?",
                    (eid,),
                )
            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (eid,)
            )
            result = build_evaluator_input(db, dict(row))
            assert result is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 4. build_evaluator_input includes injected suggestion in neutral wording
# ---------------------------------------------------------------------------


class TestBuildEvaluatorInputNeutralSuggestion:
    def test_includes_suggestion_with_neutral_framing(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_6"
            eid = _create_fire_event_with_window(db, rule, session, tmp_path)

            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (eid,)
            )
            result = build_evaluator_input(db, dict(row))
            assert result is not None

            # Suggestion must be present with neutral framing
            suggestion = result["suggestion"]
            assert "text" in suggestion
            assert suggestion["text"] == rule.action_instruction
            assert "framing" in suggestion
            assert "prior reminder suggested" in suggestion["framing"]
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 5. parse_posthoc_output validates all label/reason_code combinations
# ---------------------------------------------------------------------------


class TestParsePosthocOutput:
    def test_valid_output_parses(self):
        raw = json.dumps({
            "label": "observed_useful",
            "reason_code": "useful_prevented_error",
            "rule_application_evidence": "The assistant avoided the mistake",
            "would_likely_have_happened_without_rule": "no",
        })
        result = parse_posthoc_output(raw)
        assert result["label"] == "observed_useful"
        assert result["reason_code"] == "useful_prevented_error"

    def test_rejects_invalid_label(self):
        raw = json.dumps({
            "label": "super_useful",
            "reason_code": "useful_prevented_error",
            "rule_application_evidence": "evidence",
            "would_likely_have_happened_without_rule": "no",
        })
        with pytest.raises(ValueError, match="invalid label"):
            parse_posthoc_output(raw)

    def test_rejects_invalid_reason_code(self):
        raw = json.dumps({
            "label": "observed_useful",
            "reason_code": "made_up_reason",
            "rule_application_evidence": "evidence",
            "would_likely_have_happened_without_rule": "no",
        })
        with pytest.raises(ValueError, match="invalid reason_code"):
            parse_posthoc_output(raw)

    def test_rejects_invalid_attribution(self):
        raw = json.dumps({
            "label": "observed_useful",
            "reason_code": "useful_prevented_error",
            "rule_application_evidence": "evidence",
            "would_likely_have_happened_without_rule": "maybe",
        })
        with pytest.raises(ValueError, match="invalid attribution"):
            parse_posthoc_output(raw)

    def test_normalizes_missing_fields(self):
        raw = json.dumps({
            "label": "observed_useful",
            "reason_code": "useful_prevented_error",
        })
        result = parse_posthoc_output(raw)
        assert result["label"] == "observed_useful"
        assert result["would_likely_have_happened_without_rule"] == "unclear"

    def test_all_valid_labels_accepted(self):
        for label in POSTHOC_LABELS:
            # Pick a semantically matching reason_code for each label
            if label == "harmful":
                reason = "harmful_distracted"
            elif label == "irrelevant":
                reason = "irrelevant_not_applicable"
            elif label == "unclear":
                reason = "irrelevant_unused"
            elif label == "plausible_useful":
                reason = "useful_improved_quality"
            else:
                reason = "useful_prevented_error"
            raw = json.dumps({
                "label": label,
                "reason_code": reason,
                "rule_application_evidence": "evidence text",
                "would_likely_have_happened_without_rule": "no",
            })
            result = parse_posthoc_output(raw)
            assert result["label"] == label

    def test_strips_markdown_fences(self):
        inner = json.dumps({
            "label": "irrelevant",
            "reason_code": "irrelevant_redundant",
            "rule_application_evidence": "evidence",
            "would_likely_have_happened_without_rule": "yes",
        })
        raw = f"```json\n{inner}\n```"
        result = parse_posthoc_output(raw)
        assert result["label"] == "irrelevant"


# ---------------------------------------------------------------------------
# 6. compute_attribution_weight: observed_useful+no=1.0, +unclear=0.5, +yes=0.0
# ---------------------------------------------------------------------------


class TestComputeAttributionWeight:
    def test_observed_useful_no_is_1_0(self):
        output = {
            "label": "observed_useful",
            "would_likely_have_happened_without_rule": "no",
        }
        assert compute_attribution_weight(output) == 1.0

    def test_observed_useful_unclear_is_0_5(self):
        output = {
            "label": "observed_useful",
            "would_likely_have_happened_without_rule": "unclear",
        }
        assert compute_attribution_weight(output) == 0.5

    def test_observed_useful_yes_is_0_0(self):
        output = {
            "label": "observed_useful",
            "would_likely_have_happened_without_rule": "yes",
        }
        assert compute_attribution_weight(output) == 0.0

    def test_plausible_useful_is_0_3(self):
        output = {
            "label": "plausible_useful",
            "would_likely_have_happened_without_rule": "no",
        }
        assert compute_attribution_weight(output) == 0.3

    def test_irrelevant_is_negative_0_5(self):
        output = {
            "label": "irrelevant",
            "would_likely_have_happened_without_rule": "yes",
        }
        assert compute_attribution_weight(output) == -0.5


# ---------------------------------------------------------------------------
# 7. harmful attribution = -2.0
# ---------------------------------------------------------------------------


class TestHarmfulAttribution:
    def test_harmful_is_negative_2_0(self):
        output = {
            "label": "harmful",
            "would_likely_have_happened_without_rule": "no",
        }
        assert compute_attribution_weight(output) == -2.0

    def test_harmful_regardless_of_attribution(self):
        for attr in ATTRIBUTION_ANSWERS:
            output = {
                "label": "harmful",
                "would_likely_have_happened_without_rule": attr,
            }
            assert compute_attribution_weight(output) == -2.0


# ---------------------------------------------------------------------------
# 8. mark_posthoc_job_unclear used when window unavailable
# ---------------------------------------------------------------------------


class TestMarkPosthocJobUnclear:
    def test_marks_job_done_with_unclear_label(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_7"
            eid = _create_fire_event_with_window(db, rule, session, tmp_path)

            enqueue_posthoc_for_session(db, session)
            jobs = get_pending_posthoc_jobs(db)
            assert len(jobs) == 1

            job_id = jobs[0]["id"]
            mark_posthoc_job_unclear(db, job_id)

            # Job is no longer pending
            pending = get_pending_posthoc_jobs(db)
            assert len(pending) == 0

            # Fire event has unclear label
            row = db.fetchone(
                "SELECT posthoc_label, posthoc_reason_code "
                "FROM rule_fire_events WHERE id = ?",
                (eid,),
            )
            assert row["posthoc_label"] == "unclear"
            assert row["posthoc_reason_code"] is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 9. Pending jobs are retrievable and processable
# ---------------------------------------------------------------------------


class TestPendingJobsRetrievable:
    def test_get_pending_returns_oldest_first(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_8"

            _create_fire_event_with_window(db, rule, session, tmp_path)
            _create_fire_event_with_window(db, rule, session, tmp_path)

            enqueue_posthoc_for_session(db, session)
            jobs = get_pending_posthoc_jobs(db, limit=10)

            assert len(jobs) == 2
            # All jobs are pending
            assert all(j["status"] == "pending" for j in jobs)
            # Each job has required fields
            for j in jobs:
                assert "id" in j
                assert "fire_event_id" in j
                assert "window_payload_hash" in j

        finally:
            db.close()

    def test_limit_restricts_count(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_9"

            for _ in range(5):
                _create_fire_event_with_window(db, rule, session, tmp_path)

            enqueue_posthoc_for_session(db, session)
            jobs = get_pending_posthoc_jobs(db, limit=2)
            assert len(jobs) == 2
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 10. Completed jobs mark fire events with labels
# ---------------------------------------------------------------------------


class TestCompletedJobsMarkFireEvents:
    def test_mark_complete_propagates_label(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_10"
            eid = _create_fire_event_with_window(db, rule, session, tmp_path)

            enqueue_posthoc_for_session(db, session)
            jobs = get_pending_posthoc_jobs(db)
            job_id = jobs[0]["id"]

            mark_posthoc_job_complete(
                db, job_id, "observed_useful", "useful_prevented_error", score=0.95
            )

            # Job is done
            pending = get_pending_posthoc_jobs(db)
            assert len(pending) == 0

            # Fire event carries the label
            row = db.fetchone(
                "SELECT posthoc_label, posthoc_reason_code, posthoc_score "
                "FROM rule_fire_events WHERE id = ?",
                (eid,),
            )
            assert row["posthoc_label"] == "observed_useful"
            assert row["posthoc_reason_code"] == "useful_prevented_error"
            assert row["posthoc_score"] == pytest.approx(0.95)
        finally:
            db.close()

    def test_multiple_completions_each_label_their_event(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_11"

            eid1 = _create_fire_event_with_window(db, rule, session, tmp_path)
            eid2 = _create_fire_event_with_window(db, rule, session, tmp_path)

            enqueue_posthoc_for_session(db, session)
            jobs = get_pending_posthoc_jobs(db)

            # Complete each with a different label
            for job in jobs:
                if job["fire_event_id"] == eid1:
                    mark_posthoc_job_complete(
                        db, job["id"], "observed_useful", "useful_improved_quality"
                    )
                else:
                    mark_posthoc_job_complete(
                        db, job["id"], "irrelevant", "irrelevant_not_applicable"
                    )

            row1 = db.fetchone(
                "SELECT posthoc_label FROM rule_fire_events WHERE id = ?", (eid1,)
            )
            row2 = db.fetchone(
                "SELECT posthoc_label FROM rule_fire_events WHERE id = ?", (eid2,)
            )
            assert row1["posthoc_label"] == "observed_useful"
            assert row2["posthoc_label"] == "irrelevant"
        finally:
            db.close()

    def test_process_redundant_observed_useful_stores_irrelevant_redundant(
        self, tmp_path, monkeypatch
    ):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            session = "session_redundant"
            eid = _create_fire_event_with_window(db, rule, session, tmp_path)
            enqueue_posthoc_for_session(db, session)

            monkeypatch.setattr(
                "nokori.posthoc.jobs.run_posthoc_evaluation",
                lambda _llm, _inp: {
                    "label": "observed_useful",
                    "reason_code": "useful_prevented_error",
                    "would_likely_have_happened_without_rule": "yes",
                    "rule_application_evidence": "",
                    "attribution_weight": 0.0,
                },
            )

            summary = process_pending_posthoc_jobs(db, object())

            assert summary["done"] == 1
            row = db.fetchone(
                "SELECT posthoc_label, posthoc_reason_code, posthoc_score "
                "FROM rule_fire_events WHERE id = ?",
                (eid,),
            )
            assert row["posthoc_label"] == "irrelevant"
            assert row["posthoc_reason_code"] == "irrelevant_redundant"
            assert row["posthoc_score"] == pytest.approx(0.0)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 11. Full flywheel loop: active rule + harmful posthoc -> suppressed
# ---------------------------------------------------------------------------


class TestFullFlywheelLoop:
    """End-to-end: create active rule, fire events, run posthoc with harmful
    result, verify rule transitions to suppressed."""

    def test_harmful_posthoc_transitions_rule_to_suppressed(
        self, tmp_path, monkeypatch
    ):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db, status="active")

            # Create multiple fire events in a session
            session = "session_flywheel"
            eid1 = _create_fire_event_with_window(db, rule, session, tmp_path)
            eid2 = _create_fire_event_with_window(db, rule, session, tmp_path)

            # Enqueue posthoc jobs
            count = enqueue_posthoc_for_session(db, session)
            assert count == 2

            # Mock LLM returns harmful for all evaluations
            monkeypatch.setattr(
                "nokori.posthoc.jobs.run_posthoc_evaluation",
                lambda _llm, _inp: {
                    "label": "harmful",
                    "reason_code": "harmful_distracted",
                    "would_likely_have_happened_without_rule": "no",
                    "rule_application_evidence": "The rule caused the assistant to go off-track",
                    "attribution_weight": -2.0,
                },
            )

            # Process pending jobs — triggers score updates and lifecycle transitions
            summary = process_pending_posthoc_jobs(db, object())

            assert summary["processed"] == 2
            assert summary["done"] == 2

            # Verify fire events carry harmful labels
            for eid in (eid1, eid2):
                row = db.fetchone(
                    "SELECT posthoc_label FROM rule_fire_events WHERE id = ?",
                    (eid,),
                )
                assert row["posthoc_label"] == "harmful"

            # Verify rule transitioned to suppressed
            rule_row = db.fetchone(
                "SELECT status FROM rules WHERE id = ?", (rule.id,)
            )
            assert rule_row["status"] == "suppressed"
        finally:
            db.close()


