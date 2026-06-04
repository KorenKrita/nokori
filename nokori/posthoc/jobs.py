"""Posthoc evaluation job management.

Enqueues, fetches, and completes posthoc jobs that evaluate fire events
after a session ends. The posthoc evaluator LLM runs in cold/background
processing, not synchronously in the hook.
"""

from __future__ import annotations

import hashlib
import uuid

from ..db import Db, dumps_json, loads_json
from ..events.fire import get_fire_events_for_session, mark_posthoc_label
from .evaluator import run_posthoc_evaluation
from ..utils.time import now_iso


def enqueue_posthoc_for_session(db: Db, session_id: str) -> int:
    """Enqueue posthoc evaluation jobs for all unevaluated fire events in a session.

    Creates a posthoc_jobs entry for each fire event that lacks a posthoc_label.
    Returns count of enqueued jobs.
    """
    fire_events = get_fire_events_for_session(db, session_id)
    enqueued = 0

    with db.transaction() as tx:
        for event in fire_events:
            if event.get("posthoc_label") is not None:
                continue

            fire_event_id = event["id"]
            turn_index = event.get("turn_index")
            window_hash = compute_window_payload_hash(
                session_id, fire_event_id, turn_index
            )

            # Skip if already enqueued (idempotency via window_payload_hash)
            existing = db.fetchone(
                "SELECT id FROM posthoc_jobs WHERE fire_event_id = ?",
                (fire_event_id,),
            )
            if existing is not None:
                continue

            job_id = str(uuid.uuid4())
            now = now_iso()
            tx.execute(
                "INSERT INTO posthoc_jobs "
                "(id, fire_event_id, window_payload_hash, status, retries, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, fire_event_id, window_hash, "pending", 0, now, now),
            )
            enqueued += 1

    return enqueued


def get_pending_posthoc_jobs(db: Db, limit: int = 20) -> list[dict]:
    """Fetch pending posthoc jobs ordered by created_at (oldest first)."""
    rows = db.fetchall(
        "SELECT * FROM posthoc_jobs WHERE status = 'pending' "
        "ORDER BY created_at ASC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def mark_posthoc_job_complete(
    db: Db,
    job_id: str,
    label: str,
    reason_code: str,
    score: float | None = None,
) -> None:
    """Mark a posthoc job as done and propagate the label to the fire event."""
    now = now_iso()

    row = db.fetchone(
        "SELECT fire_event_id FROM posthoc_jobs WHERE id = ?", (job_id,)
    )
    if row is None:
        return

    fire_event_id = row["fire_event_id"]

    with db.transaction() as tx:
        tx.execute(
            "UPDATE posthoc_jobs SET status = 'done', updated_at = ? WHERE id = ?",
            (now, job_id),
        )

    mark_posthoc_label(db, fire_event_id, label, reason_code, score)


def mark_posthoc_job_failed(db: Db, job_id: str) -> None:
    """Increment retries and update timestamp for a failed posthoc job.

    The worker uses retries count and updated_at to compute exponential backoff.
    """
    now = now_iso()
    row = db.fetchone(
        "SELECT retries FROM posthoc_jobs WHERE id = ?", (job_id,)
    )
    if row is None:
        return

    new_retries = (row["retries"] or 0) + 1

    with db.transaction() as tx:
        tx.execute(
            "UPDATE posthoc_jobs SET retries = ?, updated_at = ? WHERE id = ?",
            (new_retries, now, job_id),
        )


def mark_posthoc_job_unclear(db: Db, job_id: str) -> None:
    """Mark a posthoc job as done with label='unclear' when window is unavailable."""
    mark_posthoc_job_complete(db, job_id, "unclear", "window_unavailable")


def process_pending_posthoc_jobs(db: Db, llm, *, limit: int = 20) -> dict[str, int]:
    """Evaluate pending posthoc jobs and mark each done/failed/unclear."""
    summary = {"processed": 0, "done": 0, "unclear": 0, "failed": 0}
    for job in get_pending_posthoc_jobs(db, limit=limit):
        row = db.fetchone(
            "SELECT * FROM rule_fire_events WHERE id = ?",
            (job["fire_event_id"],),
        )
        if row is None:
            mark_posthoc_job_unclear(db, job["id"])
            summary["processed"] += 1
            summary["unclear"] += 1
            continue

        evaluator_input = build_evaluator_input(db, dict(row))
        if evaluator_input is None:
            mark_posthoc_job_unclear(db, job["id"])
            summary["processed"] += 1
            summary["unclear"] += 1
            continue

        result = run_posthoc_evaluation(llm, evaluator_input)
        if result is None:
            mark_posthoc_job_failed(db, job["id"])
            summary["processed"] += 1
            summary["failed"] += 1
            continue

        label = result["label"]
        reason_code = result["reason_code"]
        attribution_weight = result.get("attribution_weight")
        if (
            label == "observed_useful"
            and result.get("would_likely_have_happened_without_rule") == "yes"
        ):
            label = "irrelevant"
            reason_code = "irrelevant_redundant"
            attribution_weight = 0.0

        mark_posthoc_job_complete(
            db,
            job["id"],
            label,
            reason_code,
            attribution_weight,
        )
        summary["processed"] += 1
        summary["done"] += 1
    return summary


def compute_window_payload_hash(
    session_id: str, fire_event_id: str, turn_index: int | None
) -> str:
    """Compute a hash for idempotency of posthoc evaluation windows."""
    payload = f"{session_id}:{fire_event_id}:{turn_index}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_evaluator_input(db: Db, fire_event: dict) -> dict | None:
    """Build the partially-blind input for the posthoc evaluator.

    Includes:
      - Injected suggestion snapshot (neutral wording)
      - Prompt context (via prompt_hash reference)
      - Bounded transcript window reference
      - Decision features at injection time

    Excludes:
      - Current rule status beyond what was injected
      - Historical usefulness scores
      - Promotion target
      - Desired label

    Returns None if window data is unavailable.
    """
    fire_event_id = fire_event.get("id")
    if not fire_event_id:
        return None

    # The bounded window ref is the key to transcript context.
    # If unavailable, the evaluator cannot judge this event.
    bounded_window_ref = fire_event.get("bounded_window_ref")
    transcript_window_ref = fire_event.get("transcript_window_ref")
    if bounded_window_ref is None and transcript_window_ref is None:
        return None

    # Build neutral suggestion from snapshots
    trigger_snapshot = fire_event.get("injected_trigger_snapshot") or ""
    action_snapshot = fire_event.get("injected_action_snapshot") or ""

    if not trigger_snapshot and not action_snapshot:
        return None

    # Neutral phrasing: "a prior reminder suggested X" (not authoritative rule)
    suggestion_text = action_snapshot if action_snapshot else trigger_snapshot

    decision_features = loads_json(
        fire_event.get("decision_features"), {}
    )

    # Fetch feedback events tied to this fire event, if any
    feedback_rows = db.fetchall(
        "SELECT source, label, confidence, evidence FROM rule_feedback_events "
        "WHERE fire_event_id = ? ORDER BY created_at ASC",
        (fire_event_id,),
    )
    feedback = [dict(r) for r in feedback_rows]

    transcript_window = (
        f"bounded_window_ref: {bounded_window_ref}"
        if bounded_window_ref
        else f"transcript_window_ref: {transcript_window_ref}"
    )
    feedback_text = dumps_json(feedback) if feedback else None

    return {
        "fire_event_id": fire_event_id,
        "session_id": fire_event.get("session_id"),
        "injected_suggestion": suggestion_text,
        "injection_context": trigger_snapshot,
        "transcript_window": transcript_window,
        "feedback": feedback_text,
        "suggestion": {
            "text": suggestion_text,
            "trigger_context": trigger_snapshot,
            "framing": "a prior reminder suggested the following",
        },
        "prompt_hash": fire_event.get("prompt_hash"),
        "bounded_window_ref": bounded_window_ref,
        "transcript_window_ref": transcript_window_ref,
        "turn_index": fire_event.get("turn_index"),
        "level": fire_event.get("level"),
        "decision_features": decision_features,
        "feedback_events": feedback,
    }
