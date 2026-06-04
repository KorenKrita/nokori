from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from ..db import Db, dumps_json
from ..utils.time import now_iso


def compute_context_fingerprint(
    prompt_hash: str,
    tool_name: str | None = None,
    turn_index: int | None = None,
) -> str:
    """SHA256 hash of prompt_hash + tool_name + turn_index for deduplication."""
    parts = [prompt_hash]
    if tool_name is not None:
        parts.append(tool_name)
    if turn_index is not None:
        parts.append(str(turn_index))
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def create_shadow_event(
    db: Db,
    rule,
    session_id: str,
    status_at_match: str,
    shadow_type: str,
    prompt_hash: str,
    matched_level: str,
    decision_features: dict,
    *,
    idf_pool_version: str | None = None,
    runtime_policy_version: str | None = None,
    embedding_profile_version: str | None = None,
    context_fingerprint: str | None = None,
) -> str:
    """Persist a shadow event for a candidate or suppressed rule match.

    Returns the generated event id.
    """
    event_id = str(uuid.uuid4())
    ts = now_iso()

    # Snapshot the rule at time of shadow match
    structured_snapshot = dumps_json({
        "concepts": rule.concepts if isinstance(rule.concepts, list) else json.loads(rule.concepts or "[]"),
        "required_concept_groups": rule.required_concept_groups if isinstance(rule.required_concept_groups, list) else json.loads(rule.required_concept_groups or "[]"),
        "excluded_contexts": rule.excluded_contexts if isinstance(rule.excluded_contexts, list) else json.loads(rule.excluded_contexts or "[]"),
        "domain_tags": rule.domain_tags,
        "tool_tags": rule.tool_tags,
        "path_patterns": rule.path_patterns,
    })

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_shadow_events "
            "(id, rule_id, session_id, shadow_rule_version, "
            "shadow_trigger_snapshot, shadow_action_snapshot, shadow_structured_snapshot, "
            "status_at_match, shadow_type, prompt_hash, matched_level, decision_features, "
            "trigger_idf_pool_version, runtime_policy_version, embedding_profile_version, "
            "context_fingerprint, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                rule.id,
                session_id,
                rule.rule_version,
                rule.trigger_canonical,
                rule.action_instruction,
                structured_snapshot,
                status_at_match,
                shadow_type,
                prompt_hash,
                matched_level,
                dumps_json(decision_features),
                idf_pool_version,
                runtime_policy_version,
                embedding_profile_version,
                context_fingerprint,
                ts,
            ),
        )
    return event_id


def get_shadow_events_for_rule(
    db: Db,
    rule_id: str,
    *,
    compatible_version: int | None = None,
    window_days: int | None = None,
) -> list[dict]:
    """Fetch shadow events for a rule.

    If compatible_version is specified, only events with matching shadow_rule_version
    are returned (per section 12: promotion counts only version-compatible evidence).
    """
    where = ["rule_id = ?"]
    params: list = [rule_id]

    if compatible_version is not None:
        where.append("shadow_rule_version = ?")
        params.append(compatible_version)

    if window_days is not None:
        cutoff = _days_ago_iso(window_days)
        where.append("created_at >= ?")
        params.append(cutoff)

    sql = (
        "SELECT * FROM rule_shadow_events WHERE "
        + " AND ".join(where)
        + " ORDER BY created_at DESC"
    )
    rows = db.fetchall(sql, tuple(params))
    return [dict(r) for r in rows]


def count_shadow_evidence(
    db: Db,
    rule_id: str,
    rule_version: int,
    window_days: int = 30,
) -> dict:
    """Count shadow labels for version-compatible events with fingerprint dedup.

    Returns counts by label plus distinct_sessions and unique_contexts.
    """
    cutoff = _days_ago_iso(window_days)

    # Use DISTINCT context_fingerprint to deduplicate repeated matches for same context.
    # NULL fingerprints are counted individually (each is unique).
    rows = db.fetchall(
        "SELECT shadow_label, session_id, context_fingerprint "
        "FROM rule_shadow_events "
        "WHERE rule_id = ? AND shadow_rule_version = ? AND created_at >= ?",
        (rule_id, rule_version, cutoff),
    )

    # Deduplicate by context_fingerprint
    seen_fingerprints: set[str] = set()
    counts: dict[str, int] = {
        "would_help_high": 0,
        "would_help_low": 0,
        "irrelevant": 0,
        "risky": 0,
        "near_miss": 0,
        "unclear": 0,
    }
    sessions: set[str] = set()
    unique_contexts = 0

    for row in rows:
        fp = row["context_fingerprint"]
        if fp is not None and fp in seen_fingerprints:
            continue
        if fp is not None:
            seen_fingerprints.add(fp)

        unique_contexts += 1
        label = row["shadow_label"]
        if label and label in counts:
            counts[label] += 1

        sid = row["session_id"]
        if sid:
            sessions.add(sid)

    return {
        **counts,
        "distinct_sessions": len(sessions),
        "unique_contexts": unique_contexts,
    }


def is_duplicate_shadow_context(
    db: Db,
    rule_id: str,
    context_fingerprint: str,
) -> bool:
    """Check if same fingerprint exists for this rule in recent window (30 days)."""
    cutoff = _days_ago_iso(30)
    row = db.fetchone(
        "SELECT 1 FROM rule_shadow_events "
        "WHERE rule_id = ? AND context_fingerprint = ? AND created_at >= ? "
        "LIMIT 1",
        (rule_id, context_fingerprint, cutoff),
    )
    return row is not None


def mark_shadow_label(
    db: Db,
    shadow_event_id: str,
    label: str,
    evaluator_model_id: str | None = None,
) -> None:
    """Update shadow event with evaluated label."""
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rule_shadow_events SET shadow_label = ?, evaluator_model_id = ? "
            "WHERE id = ?",
            (label, evaluator_model_id, shadow_event_id),
        )


def _days_ago_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
