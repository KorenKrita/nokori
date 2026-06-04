from __future__ import annotations

import json
import uuid

from ..db import Db, dumps_json, loads_json
from ..utils.time import now_iso


def create_fire_event(
    db: Db,
    rule,
    session_id: str,
    prompt_hash: str,
    level: str,
    decision_features: dict,
    *,
    turn_index: int | None = None,
    idf_pool_version: str | None = None,
    runtime_policy_version: str | None = None,
    embedding_profile_version: str | None = None,
) -> str:
    """Persist a fire event when a rule is injected into a session."""
    event_id = str(uuid.uuid4())
    now = now_iso()

    injected_structured_snapshot = dumps_json({
        "concepts": loads_json(rule.concepts, []),
        "required_concept_groups": loads_json(rule.required_concept_groups, []),
        "trigger_variants": rule.trigger_variants,
        "excluded_contexts": loads_json(rule.excluded_contexts, []),
    })

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_fire_events "
            "(id, rule_id, session_id, injected_rule_version, "
            "injected_trigger_snapshot, injected_action_snapshot, "
            "injected_structured_snapshot, "
            "trigger_idf_pool_version, runtime_policy_version, "
            "embedding_profile_version, "
            "prompt_hash, turn_index, level, decision_features, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                rule.id,
                session_id,
                rule.rule_version,
                rule.trigger_canonical,
                rule.action_instruction,
                injected_structured_snapshot,
                idf_pool_version,
                runtime_policy_version,
                embedding_profile_version,
                prompt_hash,
                turn_index,
                level,
                dumps_json(decision_features),
                now,
            ),
        )

    return event_id


def mark_posthoc_label(
    db: Db,
    fire_event_id: str,
    label: str,
    reason_code: str,
    score: float | None = None,
) -> None:
    """Update a fire event with posthoc evaluation results."""
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rule_fire_events "
            "SET posthoc_label = ?, posthoc_reason_code = ?, posthoc_score = ? "
            "WHERE id = ?",
            (label, reason_code, score, fire_event_id),
        )


def update_first_observed_useful(db: Db, rule_id: str) -> None:
    """Set rules.first_observed_useful_at if NULL and an observed_useful fire event exists."""
    row = db.fetchone(
        "SELECT first_observed_useful_at FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None or row["first_observed_useful_at"] is not None:
        return

    evidence = db.fetchone(
        "SELECT created_at FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'observed_useful' "
        "ORDER BY created_at ASC LIMIT 1",
        (rule_id,),
    )
    if evidence is None:
        return

    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET first_observed_useful_at = ?, updated_at = ? "
            "WHERE id = ? AND first_observed_useful_at IS NULL",
            (evidence["created_at"], now_iso(), rule_id),
        )


def get_fire_events_for_rule(
    db: Db, rule_id: str, limit: int | None = None
) -> list[dict]:
    """Fetch fire events for a rule ordered by created_at DESC."""
    sql = (
        "SELECT * FROM rule_fire_events WHERE rule_id = ? ORDER BY created_at DESC"
    )
    params: tuple = (rule_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (rule_id, limit)
    rows = db.fetchall(sql, params)
    return [dict(r) for r in rows]


def get_fire_events_for_session(db: Db, session_id: str) -> list[dict]:
    """Fetch fire events for a session ordered by created_at DESC."""
    rows = db.fetchall(
        "SELECT * FROM rule_fire_events WHERE session_id = ? ORDER BY created_at DESC",
        (session_id,),
    )
    return [dict(r) for r in rows]


def count_evaluated_fire_events(
    db: Db, rule_id: str, window_days: int = 30
) -> dict:
    """Count fire events by posthoc_label within a time window.

    Returns counts keyed by label plus total_evaluated.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=window_days)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")

    rows = db.fetchall(
        "SELECT posthoc_label, COUNT(*) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label IS NOT NULL AND created_at >= ? "
        "GROUP BY posthoc_label",
        (rule_id, cutoff),
    )

    counts = {
        "observed_useful": 0,
        "plausible_useful": 0,
        "irrelevant": 0,
        "harmful": 0,
        "unclear": 0,
        "total_evaluated": 0,
    }
    for row in rows:
        label = row["posthoc_label"]
        count = row["n"]
        if label in counts:
            counts[label] = count
        counts["total_evaluated"] += count

    return counts
