from __future__ import annotations

import json

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..gate import prompt_ack
from ..posthoc import enqueue_posthoc_for_session
from ..posthoc.windowing import compute_event_window, extract_window_content
from ..utils import sessions
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.session_end")


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    if cfg.disabled:
        return {"continue": True}

    session_id = effective_session_id(payload)
    sessions.end(cfg, session_id)
    ack_removed = prompt_ack.cleanup_session(cfg, session_id)
    if ack_removed:
        log.info("cleaned prompt ack/deferred session=%s files=%d", session_id, ack_removed)

    # Enqueue posthoc evaluation jobs with transcript window content (spec section 10.1)
    try:
        db = open_db(cfg.db_path)
    except DbError as e:
        log.warning("posthoc enqueue db open failed session=%s: %s", session_id, e)
        return {"continue": True}
    try:
        # Extract transcript turns from payload for windowing
        session_turns = _extract_session_turns(payload)

        # Enqueue posthoc jobs
        enqueue_posthoc_for_session(db, session_id)

        # Store bounded transcript windows in posthoc_jobs.redacted_window_json
        if session_turns:
            _populate_transcript_windows(db, session_id, session_turns, cfg)

        log.info("enqueued posthoc jobs session=%s", session_id)
    except Exception as e:
        log.warning("posthoc enqueue failed session=%s: %s", session_id, e)
    finally:
        db.close()

    return {"continue": True}


def _extract_session_turns(payload: dict) -> list[dict]:
    """Extract session turns from the session_end payload for windowing.

    Looks for turns/messages in the payload or conversation field.
    Returns list of dicts with role, content, turn_index, and optional tool fields.
    """
    turns: list[dict] = []

    # Try common payload shapes
    messages = payload.get("messages") or payload.get("conversation") or payload.get("turns")
    if not messages or not isinstance(messages, list):
        return turns

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        turn = {
            "role": msg.get("role", "unknown"),
            "content": msg.get("content", "") if isinstance(msg.get("content"), str) else str(msg.get("content", "")),
            "turn_index": msg.get("turn_index", i),
        }
        if msg.get("tool_name"):
            turn["tool_name"] = msg["tool_name"]
        if msg.get("tool_input"):
            turn["tool_input"] = str(msg["tool_input"])[:1000]
        turns.append(turn)

    return turns


def _populate_transcript_windows(
    db, session_id: str, session_turns: list[dict], cfg: Config | None = None
) -> None:
    """Store bounded transcript window content for each pending posthoc job.

    Uses windowing module to compute topic-shift-bounded windows per fire event.
    Attempts to use embedding-based topic shift detection if search.embedding is available.
    """
    # Spec section 10: session_end must only enqueue and return immediately.
    # Embedding-based topic shift is deferred to the posthoc background worker.
    embedding_fn = None

    # Get fire events for this session that have pending posthoc jobs
    rows = db.fetchall(
        "SELECT pj.id AS job_id, fe.turn_index, fe.rule_id "
        "FROM posthoc_jobs pj "
        "JOIN rule_fire_events fe ON fe.id = pj.fire_event_id "
        "WHERE fe.session_id = ? AND pj.status = 'pending' "
        "AND pj.redacted_window_json IS NULL",
        (session_id,),
    )

    for row in rows:
        turn_index = row["turn_index"]
        if turn_index is None:
            continue

        # Get rule's tool tags for relevance-based windowing
        rule_row = db.fetchone(
            "SELECT tool_tags FROM rules WHERE id = ?", (row["rule_id"],)
        )
        tool_tags = None
        if rule_row and rule_row["tool_tags"]:
            try:
                tool_tags = json.loads(rule_row["tool_tags"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Compute bounded window using topic shift detection
        window_turns = compute_event_window(
            session_turns, turn_index, tool_tags, embedding_fn=embedding_fn
        )
        window_content = extract_window_content(window_turns)

        if window_content:
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE posthoc_jobs SET redacted_window_json = ? WHERE id = ?",
                    (window_content, row["job_id"]),
                )
