from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..db import Db, dumps_json, loads_json
from ..policy import RUNTIME_POLICY_VERSION
from ..utils.time import local_days_ago, now_iso

if TYPE_CHECKING:
    from ..models import Rule


def create_fire_event(
    db: Db,
    rule: Rule,
    session_id: str,
    prompt_hash: str,
    level: str,
    decision_features: dict | None,
    *,
    decision_reason: str | None = None,
    turn_index: int | None = None,
    idf_pool_version: str | None = None,
    runtime_policy_version: str | None = None,
    embedding_profile_version: str | None = None,
    bounded_window_ref: str | None = None,
) -> str:
    """Persist a fire event when a rule is injected into a session."""
    event_id = str(uuid.uuid4())
    now = now_iso()

    resolved_decision_reason = decision_reason or (
        decision_features.get("decision_reason") if decision_features else None
    )

    injected_structured_snapshot = dumps_json(
        {
            "concepts": rule.concepts
            if isinstance(rule.concepts, list)
            else loads_json(rule.concepts, []),
            "required_concept_groups": rule.required_concept_groups
            if isinstance(rule.required_concept_groups, list)
            else loads_json(rule.required_concept_groups, []),
            "trigger_variants": rule.trigger_variants
            if isinstance(rule.trigger_variants, list)
            else loads_json(rule.trigger_variants, []),
            "excluded_contexts": rule.excluded_contexts
            if isinstance(rule.excluded_contexts, list)
            else loads_json(rule.excluded_contexts, []),
            "domain_tags": rule.domain_tags,
            "tool_tags": rule.tool_tags,
            "path_patterns": rule.path_patterns,
        }
    )

    transcript_window_ref = (
        f"session:{session_id}:turn:{turn_index}"
        if turn_index is not None
        else f"session:{session_id}"
    )

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_fire_events "
            "(id, rule_id, session_id, injected_rule_version, "
            "injected_trigger_snapshot, injected_action_snapshot, "
            "injected_structured_snapshot, "
            "trigger_idf_pool_version, runtime_policy_version, "
            "embedding_profile_version, "
            "prompt_hash, transcript_window_ref, turn_index, level, "
            "decision_reason, decision_features, "
            "bounded_window_ref, "
            "created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                rule.id,
                session_id,
                rule.rule_version,
                rule.trigger_canonical,
                rule.action_instruction,
                injected_structured_snapshot,
                idf_pool_version,
                runtime_policy_version or RUNTIME_POLICY_VERSION,
                embedding_profile_version,
                prompt_hash,
                transcript_window_ref,
                turn_index,
                level,
                resolved_decision_reason,
                dumps_json(decision_features),
                bounded_window_ref,
                now,
            ),
        )

    return event_id


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


def get_fire_events_for_rule(db: Db, rule_id: str, limit: int | None = None) -> list[dict]:
    """Fetch fire events for a rule ordered by created_at DESC."""
    sql = "SELECT * FROM rule_fire_events WHERE rule_id = ? ORDER BY created_at DESC"
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


def count_distinct_useful_projects(db: Db, rule_id: str) -> int:
    """Count distinct project_ids with observed_useful fire events for a rule."""
    row = db.fetchone(
        "SELECT COUNT(DISTINCT project_id) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'observed_useful' "
        "AND project_id IS NOT NULL",
        (rule_id,),
    )
    return int(row["n"]) if row else 0


def batch_count_distinct_useful_projects(db: Db, rule_ids: list[str]) -> dict[str, int]:
    """Batch version: count distinct useful projects for multiple rules at once."""
    if not rule_ids:
        return {}
    placeholders = ",".join("?" * len(rule_ids))
    rows = db.fetchall(
        f"SELECT rule_id, COUNT(DISTINCT project_id) AS n FROM rule_fire_events "
        f"WHERE rule_id IN ({placeholders}) AND posthoc_label = 'observed_useful' "
        f"AND project_id IS NOT NULL GROUP BY rule_id",
        tuple(rule_ids),
    )
    return {row["rule_id"]: int(row["n"]) for row in rows}


def count_evaluated_fire_events(db: Db, rule_id: str, window_days: int = 30) -> dict:
    """Count fire events by posthoc_label within a time window.

    Returns counts keyed by label plus total_evaluated.
    """
    cutoff = local_days_ago(window_days)

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
