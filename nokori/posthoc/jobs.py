"""Posthoc evaluation job management.

Enqueues, fetches, and completes posthoc jobs that evaluate fire events
after a session ends. The posthoc evaluator LLM runs in cold/background
processing, not synchronously in the hook.
"""

from __future__ import annotations

import hashlib
import uuid

from ..db import Db, dumps_json, loads_json
from ..events.fire import get_fire_events_for_session, mark_posthoc_label, update_first_observed_useful
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
        tx.execute(
            "UPDATE rule_fire_events "
            "SET posthoc_label = ?, posthoc_reason_code = ?, posthoc_score = ? "
            "WHERE id = ?",
            (label, reason_code, score, fire_event_id),
        )


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
    mark_posthoc_job_complete(db, job_id, "unclear", None)


def process_pending_posthoc_jobs(db: Db, llm, *, limit: int = 20) -> dict[str, int]:
    """Evaluate pending posthoc jobs, mark done, update scores, trigger transitions."""
    from ..lifecycle.transitions import evaluate_transitions, update_derived_scores

    summary = {"processed": 0, "done": 0, "unclear": 0, "failed": 0}
    rules_to_update: set[str] = set()

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
            # Spec 10.4: check if feedback events exist supporting this rule
            fire_event_id = job["fire_event_id"]
            supporting_feedback = db.fetchone(
                "SELECT id FROM rule_feedback_events "
                "WHERE fire_event_id = ? AND label = 'helped'",
                (fire_event_id,),
            )
            if supporting_feedback:
                # Feedback supports the rule: keep as observed_useful with weak weight
                attribution_weight = 0.3
            else:
                # No feedback exists: convert to irrelevant
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

        # Track rules that need score/lifecycle update
        # Spec 10.3: unclear = no state update
        rule_id = row["rule_id"]
        if rule_id and label != "unclear":
            rules_to_update.add(rule_id)
            if label == "observed_useful":
                update_first_observed_useful(db, rule_id)

    # Update derived scores and evaluate lifecycle transitions for affected rules
    for rule_id in rules_to_update:
        update_derived_scores(db, rule_id)
        evaluate_transitions(db, rule_id)

    return summary


def submit_feedback(
    db: Db,
    fire_event_id: str,
    source: str,
    label: str,
    confidence: float,
    evidence: str,
    session_id: str | None = None,
) -> str | None:
    """Submit agent/CLI feedback tied to a recent fire event (spec section 10.4).

    Constraints:
    - Must reference a fire event from the current or recent session.
    - Cannot change rule text.
    - Cannot promote to trusted by itself.
    - High-confidence harmful feedback can trigger suppression after posthoc confirmation.

    Returns feedback event id, or None if validation fails.
    """
    # Validate fire event exists and is recent
    fire_row = db.fetchone(
        "SELECT rule_id, session_id, created_at FROM rule_fire_events WHERE id = ?",
        (fire_event_id,),
    )
    if fire_row is None:
        return None

    # Validate label
    valid_labels = ("helped", "irrelevant", "harmful", "unclear")
    if label not in valid_labels:
        return None

    # Validate confidence range
    if not (0.0 <= confidence <= 1.0):
        return None

    # Validate source
    valid_sources = ("agent_cli",)
    if source not in valid_sources:
        return None

    # Recency check: fire event must be from this session or within 24h
    from datetime import datetime, timedelta, timezone
    from ..utils.time import parse_iso

    if session_id and fire_row["session_id"] == session_id:
        pass  # Same session, no recency constraint
    else:
        fire_time = parse_iso(fire_row["created_at"])
        if fire_time and (datetime.now(timezone.utc) - fire_time) > timedelta(hours=24):
            return None

    # Rate-limiting: max 5 feedback events per rule per day
    rule_id = fire_row["rule_id"]
    from datetime import datetime, timedelta, timezone
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    count_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rule_feedback_events f "
        "JOIN rule_fire_events e ON e.id = f.fire_event_id "
        "WHERE e.rule_id = ? AND f.created_at >= ?",
        (rule_id, day_ago),
    )
    if count_row and count_row["n"] >= 5:
        return None

    feedback_id = str(uuid.uuid4())
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_feedback_events "
            "(id, fire_event_id, source, label, confidence, evidence, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (feedback_id, fire_event_id, source, label, confidence, evidence, now),
        )

    # High-confidence harmful feedback: suppress after posthoc confirmation
    # OR direct deterministic harm evidence (spec 10.4).
    if label == "harmful" and confidence >= 0.9 and rule_id:
        posthoc_confirmed = db.fetchone(
            "SELECT posthoc_label FROM rule_fire_events WHERE id = ? AND posthoc_label = 'harmful'",
            (fire_event_id,),
        )
        has_deterministic_harm = _is_deterministic_harm_evidence(evidence)
        if posthoc_confirmed or has_deterministic_harm:
            from ..lifecycle.transitions import _apply_transition

            rule_row_full = db.fetchone(
                "SELECT rule_version, status, runtime_policy_version FROM rules WHERE id = ?",
                (rule_id,),
            )
            if rule_row_full and rule_row_full["status"] in ("active", "trusted"):
                reason = (
                    f"harmful_feedback_deterministic (confidence={confidence:.2f})"
                    if has_deterministic_harm
                    else f"harmful_feedback_with_posthoc_confirmation (confidence={confidence:.2f})"
                )
                _apply_transition(
                    db, rule_id, rule_row_full["rule_version"],
                    rule_row_full["status"], "suppressed",
                    rule_row_full["runtime_policy_version"],
                    reason,
                )

    return feedback_id


_DETERMINISTIC_HARM_MARKERS = (
    "build failed",
    "test failed",
    "error:",
    "exception:",
    "traceback",
    "command failed",
    "exit code",
    "permission denied",
    "data loss",
    "deleted",
    "overwritten",
    "corrupted",
)


def _is_deterministic_harm_evidence(evidence: str) -> bool:
    """Check if feedback evidence contains machine-verifiable harm signals."""
    if not evidence or len(evidence) < 20:
        return False
    lower = evidence.lower()
    return any(marker in lower for marker in _DETERMINISTIC_HARM_MARKERS)


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
      - Bounded transcript window content (loaded from posthoc_jobs or session data)
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

    # Use bounded_window_ref as injection_context if it contains actual prompt text
    # (> 64 chars means it's real content, not just a hash reference).
    if bounded_window_ref and len(bounded_window_ref) > 64:
        injection_context = bounded_window_ref
    else:
        injection_context = trigger_snapshot

    decision_features = loads_json(
        fire_event.get("decision_features"), {}
    )
    # Spec 10.2: evaluator must NOT see status-revealing fields.
    # Strip decision_reason which may contain status/severity info.
    decision_features.pop("decision_reason", None)

    # Fetch feedback events tied to this fire event, if any
    feedback_rows = db.fetchall(
        "SELECT source, label, confidence, evidence FROM rule_feedback_events "
        "WHERE fire_event_id = ? ORDER BY created_at ASC",
        (fire_event_id,),
    )
    feedback = [dict(r) for r in feedback_rows]

    # Spec 10.4: down-weight repeated feedback without transcript/tool evidence.
    # Detect repeated low-evidence feedback from same source and annotate weight.
    feedback = _annotate_feedback_weights(feedback)

    # Load actual transcript window content from posthoc_jobs redacted_window_json
    transcript_window_content = _load_transcript_window(
        db, fire_event_id, bounded_window_ref, transcript_window_ref
    )
    if transcript_window_content is None:
        return None

    feedback_text = dumps_json(feedback) if feedback else None

    return {
        "fire_event_id": fire_event_id,
        "session_id": fire_event.get("session_id"),
        "injected_suggestion": suggestion_text,
        "injection_context": injection_context,
        "transcript_window": transcript_window_content,
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
        "decision_features": decision_features,
        "feedback_events": feedback,
    }


def _annotate_feedback_weights(feedback: list[dict]) -> list[dict]:
    """Down-weight repeated low-evidence feedback from the same source (spec 10.4)."""
    if not feedback:
        return feedback
    source_counts: dict[str, int] = {}
    result = []
    for fb in feedback:
        src = fb.get("source", "")
        source_counts[src] = source_counts.get(src, 0) + 1
        evidence_text = fb.get("evidence", "")
        is_low_evidence = not evidence_text or len(evidence_text) < 30
        repeat_count = source_counts[src]
        if is_low_evidence and repeat_count > 1:
            fb = {**fb, "weight": max(0.1, 1.0 / repeat_count)}
        else:
            fb = {**fb, "weight": 1.0}
        result.append(fb)
    return result


def _load_transcript_window(
    db: Db,
    fire_event_id: str,
    bounded_window_ref: str | None,
    transcript_window_ref: str | None,
) -> str | None:
    """Load actual transcript window content for posthoc evaluation.

    Tries in order:
    1. posthoc_jobs.redacted_window_json for this fire event
    2. The bounded_window_ref/transcript_window_ref as stored content

    Returns None if no actual content is available (not just a hash ref).
    """
    # Check if we stored the window payload in posthoc_jobs
    job_row = db.fetchone(
        "SELECT redacted_window_json FROM posthoc_jobs WHERE fire_event_id = ?",
        (fire_event_id,),
    )
    if job_row and job_row["redacted_window_json"]:
        content = job_row["redacted_window_json"]
        if content and len(content) > 50:
            return content

    # The bounded_window_ref may contain inline content (stored during session_end)
    # or be a reference identifier. Refs that look like hex hashes (16-64 hex chars)
    # are just IDs without real content — mark as unclear rather than wrapping as fake content.
    import re as _re
    ref = bounded_window_ref or transcript_window_ref
    if ref:
        if _re.match(r'^[0-9a-f]{16,64}$', ref):
            return None
        return ref

    return None
