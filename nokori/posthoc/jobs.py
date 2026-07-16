"""Posthoc evaluation job management.

Enqueues, fetches, and completes posthoc jobs that evaluate fire events
after a session ends. The posthoc evaluator LLM runs in cold/background
processing, not synchronously in the hook.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from ..db import Db, loads_json
from ..events.fire import get_fire_events_for_session, update_first_observed_useful
from ..events.observability import write_event
from ..extract.reader import read as read_transcript
from ..gate.marker import prompt_hash
from ..posthoc.windowing import compute_event_window, extract_window_content
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash
from ..utils.time import now_iso
from ..utils.transcript import is_path_allowed
from .evaluator import PosthocLlmCaller, run_posthoc_evaluation

log = get_logger("nokori.posthoc.jobs")

# Minimum length for a redacted window to count as real content vs a stub/placeholder.
_MIN_REDACTED_WINDOW_LEN = 50


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
            window_hash = compute_window_payload_hash(session_id, fire_event_id, turn_index)

            # Skip if already enqueued (one posthoc job per fire event)
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
    """Fetch pending posthoc jobs with exponential backoff on retried jobs."""
    now = now_iso()
    rows = db.fetchall(
        "SELECT * FROM posthoc_jobs WHERE status = 'pending' "
        "AND (retries = 0 OR "
        "  datetime(updated_at, '+' || (30 * (1 << MIN(retries, 8))) || ' seconds') <= datetime(?)) "
        "ORDER BY retries ASC, created_at ASC LIMIT ?",
        (now, limit),
    )
    return [dict(r) for r in rows]


def mark_posthoc_job_complete(
    db: Db,
    job_id: str,
    label: str,
    reason_code: str | None,
    score: float | None = None,
) -> None:
    """Mark a posthoc job as done and propagate the label to the fire event."""
    now = now_iso()

    row = db.fetchone("SELECT fire_event_id FROM posthoc_jobs WHERE id = ?", (job_id,))
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


_POSTHOC_MAX_RETRIES = 5


def mark_posthoc_job_failed(db: Db, job_id: str) -> None:
    """Increment retries. After max retries, mark as permanently failed."""
    now = now_iso()
    row = db.fetchone("SELECT retries FROM posthoc_jobs WHERE id = ?", (job_id,))
    if row is None:
        return

    new_retries = (row["retries"] or 0) + 1
    new_status = "failed" if new_retries >= _POSTHOC_MAX_RETRIES else "pending"

    with db.transaction() as tx:
        tx.execute(
            "UPDATE posthoc_jobs SET retries = ?, status = ?, updated_at = ? WHERE id = ?",
            (new_retries, new_status, now, job_id),
        )


def mark_posthoc_job_unclear(db: Db, job_id: str) -> None:
    """Mark a posthoc job as done with label='unclear' when window is unavailable."""
    mark_posthoc_job_complete(db, job_id, "unclear", None)


def process_pending_posthoc_jobs(db: Db, llm: PosthocLlmCaller, *, limit: int = 20) -> dict[str, int]:
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
        write_event(
            db,
            source="posthoc_evaluation",
            outcome=label,
            details={
                "rule_id": row["rule_id"],
                "fire_event_id": job["fire_event_id"],
                "reason_code": reason_code,
                "attribution_weight": attribution_weight,
            },
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
        try:
            update_derived_scores(db, rule_id)
            evaluate_transitions(db, rule_id)
        except Exception as exc:
            log.exception("lifecycle update failed for rule=%s: %s", rule_id, exc)

    return summary


def compute_window_payload_hash(session_id: str, fire_event_id: str, turn_index: int | None) -> str:
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

    bounded_window_ref = fire_event.get("bounded_window_ref")
    transcript_window_ref = fire_event.get("transcript_window_ref")
    # Early exit only if both are truly absent (legacy events before transcript_window_ref was added)
    if bounded_window_ref is None and transcript_window_ref is None:
        return None

    # Build neutral suggestion from snapshots
    trigger_snapshot = fire_event.get("injected_trigger_snapshot") or ""
    action_snapshot = fire_event.get("injected_action_snapshot") or ""

    if not trigger_snapshot and not action_snapshot:
        return None

    # Neutral phrasing: "a prior reminder suggested X" (not authoritative rule)
    suggestion_text = action_snapshot if action_snapshot else trigger_snapshot

    # injection_context must be clean rule-snapshot data, never the raw
    # user_prompt_submit prompt text (which may contain skill system prompts /
    # task-notifications — see task 06-18-fix-posthoc-window-active-fire-loop).
    injection_context = trigger_snapshot

    decision_features = loads_json(fire_event.get("decision_features"), {})
    # Spec 10.2: evaluator must NOT see status-revealing fields or quality scores.
    _BLIND_KEYS = (
        "decision_reason",
        "quality_score",
        "evidence_support_score",
        "specificity_score",
        "retrieval_readiness_score",
        "observed_usefulness_score",
        "plausible_usefulness_score",
        "false_positive_score",
        "harmful_score",
        "status",
        "severity",
        "activation_origin",
        "merge_lineage",
    )
    for k in _BLIND_KEYS:
        decision_features.pop(k, None)

    # Load actual transcript window content from posthoc_jobs redacted_window_json
    transcript_window_content = _load_transcript_window(
        db,
        fire_event_id,
        fire_event.get("session_id"),
        fire_event.get("prompt_hash"),
        fire_event.get("turn_index"),
        bounded_window_ref,
        transcript_window_ref,
        fire_event.get("injected_structured_snapshot"),
    )
    if transcript_window_content is None:
        return None

    return {
        "fire_event_id": fire_event_id,
        "session_id": fire_event.get("session_id"),
        "injected_suggestion": suggestion_text,
        "injection_context": injection_context,
        "transcript_window": transcript_window_content,
        "feedback": None,
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
        "feedback_events": None,
    }


def _load_transcript_window(
    db: Db,
    fire_event_id: str,
    _session_id: str | None,  # reserved for future logging; not used currently
    prompt_hash_value: str | None,
    turn_index: int | None,
    bounded_window_ref: str | None,
    _transcript_window_ref: str | None,  # unused in new impl; kept for signature compat
    injected_structured_snapshot: str | None,
) -> str | None:
    """Load actual transcript window content for posthoc evaluation.

    Tries in order:
    1. posthoc_jobs.redacted_window_json for this fire event (precomputed at
       session_end by _populate_transcript_windows).
    2. Re-derive the window at evaluation time by reading the transcript file
       referenced by bounded_window_ref ("transcript:<path>") and locating the
       injection turn via prompt_hash. This handles fire events whose
       session_end did not populate redacted_window_json (e.g. turn_index was
       None, or the session_end payload lacked messages).

    Returns None when no real transcript window is available — caller then
    marks the job unclear instead of poisoning the evaluator with the raw
    user_prompt_submit prompt text (which may be a skill system prompt).
    """
    # 1. Precomputed window stored at session_end
    job_row = db.fetchone(
        "SELECT redacted_window_json FROM posthoc_jobs WHERE fire_event_id = ?",
        (fire_event_id,),
    )
    if job_row and job_row["redacted_window_json"]:
        content: str = job_row["redacted_window_json"]
        if content and len(content) > _MIN_REDACTED_WINDOW_LEN:
            return content

    # 2. Re-derive from transcript file + prompt_hash.
    transcript_path = _parse_transcript_ref(bounded_window_ref)
    if transcript_path is None:
        # Old-format bounded_window_ref (raw prompt text) or session: ref —
        # no real transcript window obtainable. Skip rather than poison the
        # evaluator with the (likely irrelevant) prompt text.
        return None

    return _compute_window_from_transcript(
        transcript_path,
        prompt_hash_value,
        turn_index,
        injected_structured_snapshot,
    )


def _parse_transcript_ref(bounded_window_ref: str | None) -> Path | None:
    """Extract a transcript Path from a "transcript:<path>" bounded_window_ref.

    Returns None for legacy formats (raw prompt text, "session:..." refs, hex
    hashes). The path is validated against the transcript allowed-roots to
    prevent path traversal from maliciously crafted refs.
    """
    if not bounded_window_ref:
        return None
    if not bounded_window_ref.startswith("transcript:"):
        return None
    raw = bounded_window_ref[len("transcript:") :]
    if not raw:
        return None
    path = Path(raw).expanduser()
    # Align with resolve_transcript_path's validation: require .jsonl suffix so
    # a maliciously crafted ref can't point us at arbitrary allowed-root files
    # (configs, caches) and feed them to the evaluator.
    if path.suffix.lower() != ".jsonl":
        log.warning(
            "bounded_window_ref transcript path is not a .jsonl file: %s", path
        )
        return None
    if not is_path_allowed(path):
        log.warning(
            "bounded_window_ref transcript path outside allowed roots: %s", path
        )
        return None
    if not path.is_file():
        return None
    return path


def _compute_window_from_transcript(
    transcript_path: Path,
    prompt_hash_value: str | None,
    turn_index: int | None,
    injected_structured_snapshot: str | None,
) -> str | None:
    """Read transcript, locate injection turn, return bounded window content.

    Locates the injection turn by:
    1. turn_index match (when present and in range).
    2. prompt_hash match — normalize each user turn's content and hash it,
       comparing to the fire event's prompt_hash. This handles UserPromptSubmit
       payloads that lack turn_index (Claude Code / Cursor).

    Returns None when the transcript is missing, unreadable, or no matching
    injection turn is found — caller skips posthoc rather than guessing.
    """
    if not transcript_path.exists():
        return None

    try:
        turns = read_transcript(transcript_path)
    except Exception as e:
        log.warning("transcript read failed path=%s: %s", transcript_path, e)
        return None
    if not turns:
        return None

    # Build the windowing input shape: list of dicts with turn_index assigned
    # by position. compute_event_window looks up turns by turn_index.
    session_turns: list[dict] = []
    injection_turn_index: int | None = None

    for idx, turn in enumerate(turns):
        session_turns.append(
            {
                # Normalize "human" → "user": compute_event_window's topic-shift
                # and stop conditions check role == "user", so an unnormalized
                # "human" would be treated as a non-user turn and skew the window.
                "role": "user" if turn.role == "human" else turn.role,
                "content": turn.content,
                "turn_index": idx,
                "tool_name": turn.tool_name,
                "tool_input": turn.input_summary,
            }
        )

    # Prefer turn_index when present and within range; tolerate string values
    # from hook payloads / SQLite rows. When prompt_hash is also available,
    # validate the candidate turn actually corresponds to the triggering prompt
    # to avoid selecting a mis-offset window.
    turn_index_int: int | None = None
    if turn_index is not None:
        try:
            turn_index_int = int(turn_index)
        except (TypeError, ValueError):
            turn_index_int = None
    if turn_index_int is not None and 0 <= turn_index_int < len(session_turns):
        candidate = session_turns[turn_index_int]
        # The triggering turn must be a user turn (rules fire on user prompt
        # submit). A non-user turn (assistant/tool) means the turn_index is
        # mis-offset — reject and fall through to hash scan.
        if candidate["role"] != "user":
            turn_index_int = None
        elif prompt_hash_value:
            normalized_cand = normalize_prompt_for_hash(candidate["content"])
            if prompt_hash(normalized_cand or candidate["content"]) != prompt_hash_value:
                turn_index_int = None
        if turn_index_int is not None:
            injection_turn_index = turn_index_int

    # Fall back to prompt_hash matching against user turns.
    # Hash caliber mirrors prompt_inject.py: prompt_hash(normalized or content)
    if injection_turn_index is None and prompt_hash_value:
        for idx, turn in enumerate(turns):
            if turn.role != "human":
                continue
            normalized = normalize_prompt_for_hash(turn.content)
            if not normalized and not turn.content:
                continue
            if prompt_hash(normalized or turn.content) == prompt_hash_value:
                injection_turn_index = idx
                break

    if injection_turn_index is None:
        return None

    # Extract rule tool_tags from injected_structured_snapshot for relevance
    # windowing (matches _populate_transcript_windows behavior).
    tool_tags: list[str] | None = None
    if injected_structured_snapshot:
        try:
            structured = loads_json(injected_structured_snapshot, {})
            raw_tags = structured.get("tool_tags")
            if isinstance(raw_tags, list):
                tool_tags = [str(t) for t in raw_tags if isinstance(t, str)]
        except Exception:
            tool_tags = None

    window_turns = compute_event_window(
        session_turns, injection_turn_index, tool_tags, embedding_fn=None
    )
    if not window_turns:
        return None

    return extract_window_content(window_turns)
