from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from ..db import Db, dumps_json, loads_json
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
        "concepts": rule.concepts if isinstance(rule.concepts, list) else loads_json(rule.concepts, []),
        "required_concept_groups": rule.required_concept_groups if isinstance(rule.required_concept_groups, list) else loads_json(rule.required_concept_groups, []),
        "excluded_contexts": rule.excluded_contexts if isinstance(rule.excluded_contexts, list) else loads_json(rule.excluded_contexts, []),
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
    event_limit: int = 10,
    shadow_type: str | None = None,
    since_iso: str | None = None,
) -> dict:
    """Count shadow labels for version-compatible events with fingerprint dedup.

    Uses SHADOW_EVENT_WINDOW (last N evaluated events) per spec section 3.4.
    Optionally filters by shadow_type (candidate_probe or suppression_recovery).
    Optionally filters to events created after since_iso (for recovery evidence).
    Returns counts by label plus distinct_sessions, unique_contexts, and per_session_counts.
    """
    cutoff = since_iso if since_iso else _days_ago_iso(window_days)

    # SHADOW_EVENT_WINDOW: use last N events (spec 3.4)
    type_filter = ""
    params: list = [rule_id, rule_version, cutoff]
    if shadow_type:
        type_filter = "AND shadow_type = ? "
        params.append(shadow_type)
    params.append(event_limit)

    sql = (
        "SELECT shadow_label, session_id, context_fingerprint "
        "FROM rule_shadow_events "
        "WHERE rule_id = ? AND shadow_rule_version = ? "
        "AND shadow_label IS NOT NULL AND created_at >= ? "
        f"{type_filter}"
        "ORDER BY created_at DESC LIMIT ?"
    )
    rows = db.fetchall(sql, tuple(params))

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
    # Per-session strong counts for single-session exception
    per_session_strong: dict[str, int] = {}

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
            if label == "would_help_high":
                per_session_strong[sid] = per_session_strong.get(sid, 0) + 1

    # Best single-session strong count
    best_single_session_strong = max(per_session_strong.values()) if per_session_strong else 0

    return {
        **counts,
        "distinct_sessions": len(sessions),
        "unique_contexts": unique_contexts,
        "best_single_session_strong": best_single_session_strong,
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


def get_unlabeled_shadow_events(db: Db, limit: int = 20) -> list[dict]:
    """Fetch shadow events that have no shadow_label for counterfactual evaluation."""
    rows = db.fetchall(
        "SELECT * FROM rule_shadow_events "
        "WHERE shadow_label IS NULL "
        "ORDER BY created_at ASC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def run_shadow_counterfactual_evaluation(
    db: Db, llm, *, limit: int = 20
) -> dict[str, int]:
    """Evaluate unlabeled shadow events via LLM counterfactual analysis.

    For each shadow event, determines whether the rule would have helped
    if it had been injected. Produces shadow labels:
    would_help_high, would_help_low, irrelevant, risky, near_miss, unclear.

    This is the shadow counterfactual evaluation pipeline that enables
    candidate promotion and suppressed recovery (spec section 10.3).
    """
    summary = {"processed": 0, "labeled": 0, "failed": 0}

    events = get_unlabeled_shadow_events(db, limit=limit)
    for event in events:
        shadow_event_id = event["id"]
        action_snapshot = event.get("shadow_action_snapshot", "")
        trigger_snapshot = event.get("shadow_trigger_snapshot", "")

        if not action_snapshot and not trigger_snapshot:
            mark_shadow_label(db, shadow_event_id, "unclear")
            summary["processed"] += 1
            summary["labeled"] += 1
            continue

        suggestion_text = action_snapshot or trigger_snapshot

        system_prompt = (
            "You are a counterfactual evaluator for a rule memory system. "
            "You will see a rule suggestion and the context where it matched "
            "(but was NOT injected). Judge whether the suggestion WOULD HAVE "
            "helped the assistant if it had been shown.\n\n"
            "Labels:\n"
            "- would_help_high: Strong evidence the suggestion would have "
            "prevented an error or improved the outcome significantly.\n"
            "- would_help_low: Some evidence the suggestion might have helped, "
            "but the outcome was already acceptable.\n"
            "- irrelevant: The suggestion does not apply to this context.\n"
            "- risky: The suggestion could have caused harm in this context.\n"
            "- near_miss: The context is superficially similar but the rule "
            "trigger does not actually apply.\n"
            "- unclear: Insufficient information to judge.\n\n"
            "Return JSON: {\"label\": \"...\", \"reasoning\": \"...\"}"
        )

        user_prompt = (
            f"## Rule Suggestion\n"
            f"A prior reminder would have suggested: {suggestion_text}\n\n"
            f"## Trigger Context\n{trigger_snapshot}\n\n"
            f"## Match Context\n"
            f"prompt_hash: {event.get('prompt_hash', 'unknown')}\n"
            f"matched_level: {event.get('matched_level', 'unknown')}\n"
        )

        try:
            import json
            response = llm.call(
                system=system_prompt,
                user=user_prompt,
                role="posthoc_evaluator",
            )
            data = json.loads(response)
            label = data.get("label", "unclear")
            valid_labels = (
                "would_help_high", "would_help_low", "irrelevant",
                "risky", "near_miss", "unclear",
            )
            if label not in valid_labels:
                label = "unclear"
            mark_shadow_label(db, shadow_event_id, label)
            summary["labeled"] += 1
        except Exception:
            summary["failed"] += 1

        summary["processed"] += 1

    return summary


def _days_ago_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
