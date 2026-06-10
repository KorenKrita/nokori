from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ..db import Db, dumps_json, loads_json
from ..events.observability import write_event
from ..llm.json_payload import parse_json_payload
from ..utils.time import now_iso

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..models import Rule


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
    rule: "Rule",
    session_id: str,
    status_at_match: str,
    shadow_type: str,
    prompt_hash: str,
    matched_level: str,
    decision_features: dict,
    *,
    bounded_window_ref: str | None = None,
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
            "status_at_match, shadow_type, prompt_hash, bounded_window_ref, "
            "matched_level, decision_features, "
            "trigger_idf_pool_version, runtime_policy_version, embedding_profile_version, "
            "context_fingerprint, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                bounded_window_ref,
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
    Returns counts by label plus distinct_sessions, unique_contexts, per_session_counts,
    and task_deduped_count (semantic task-level dedup within sessions).
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
        "SELECT shadow_label, session_id, context_fingerprint, prompt_hash, created_at "
        "FROM rule_shadow_events "
        "WHERE rule_id = ? AND shadow_rule_version = ? "
        "AND shadow_label IS NOT NULL AND shadow_label != 'unclear' AND created_at >= ? "
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
    }
    sessions: set[str] = set()
    unique_contexts = 0
    # Per-session strong counts for single-session exception
    per_session_strong: dict[str, int] = {}
    # Per-session unique context counts for diversity check
    per_session_contexts: dict[str, set] = {}
    # Collect fingerprint-deduped rows for task-level dedup pass
    deduped_rows: list[dict] = []

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
            # Track per-session distinct contexts
            if sid not in per_session_contexts:
                per_session_contexts[sid] = set()
            if fp is not None:
                per_session_contexts[sid].add(fp)

        deduped_rows.append(dict(row))

    # Best single-session strong count
    best_single_session_strong = max(per_session_strong.values()) if per_session_strong else 0

    # Best single-session's context diversity (for single-session exception)
    best_session_id = max(per_session_strong, key=per_session_strong.get) if per_session_strong else None
    best_single_session_contexts = (
        len(per_session_contexts.get(best_session_id, set())) if best_session_id else 0
    )

    # --- Semantic task-level dedup ---
    # Within each session, events sharing prompt_hash prefix (first 8 chars) or
    # occurring within 3 consecutive turn_indexes likely belong to the same task
    # and count as ONE sample.
    task_deduped_count = _compute_task_deduped_count(deduped_rows)

    return {
        **counts,
        "distinct_sessions": len(sessions),
        "unique_contexts": unique_contexts,
        "best_single_session_strong": best_single_session_strong,
        "best_single_session_contexts": best_single_session_contexts,
        "task_deduped_count": task_deduped_count,
    }


def _compute_task_deduped_count(deduped_rows: list[dict]) -> int:
    """Compute task-level deduped sample count from fingerprint-deduped rows.

    Groups events by session_id. Within each session, events that share the same
    prompt_hash prefix (first 8 chars) OR occur within 3 consecutive positions
    (by created_at ordering) are collapsed into a single task sample.

    Returns the effective sample count after task dedup.
    """
    if not deduped_rows:
        return 0

    # Group by session
    by_session: dict[str, list[dict]] = {}
    no_session: list[dict] = []
    for row in deduped_rows:
        sid = row.get("session_id")
        if sid:
            by_session.setdefault(sid, []).append(row)
        else:
            no_session.append(row)

    total_tasks = 0

    for _sid, session_rows in by_session.items():
        # Sort by created_at for consecutive-event grouping (proxy for turn order)
        session_rows.sort(key=lambda r: r.get("created_at") or "")
        # Track which rows have been assigned to a task group
        assigned = [False] * len(session_rows)

        for i in range(len(session_rows)):
            if assigned[i]:
                continue
            # Start a new task group from this row
            assigned[i] = True
            group_prefix = (session_rows[i].get("prompt_hash") or "")[:8]

            for j in range(i + 1, len(session_rows)):
                if assigned[j]:
                    continue
                j_prefix = (session_rows[j].get("prompt_hash") or "")[:8]

                # Same task if prompt_hash prefix matches
                same_prefix = (
                    group_prefix
                    and j_prefix
                    and group_prefix == j_prefix
                )
                # Same task if within 3 consecutive positions from group start
                consecutive = (j - i) <= 3

                if same_prefix or consecutive:
                    assigned[j] = True

            total_tasks += 1

    # Events without session_id each count as one task
    total_tasks += len(no_session)

    return total_tasks


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
    summary = {"processed": 0, "labeled": 0, "failed": 0, "transitions_applied": 0}
    affected_rule_ids: set[str] = set()

    events = get_unlabeled_shadow_events(db, limit=limit)
    for event in events:
        shadow_event_id = event["id"]
        rule_id = event.get("rule_id")
        action_snapshot = event.get("shadow_action_snapshot", "")
        trigger_snapshot = event.get("shadow_trigger_snapshot", "")

        if not action_snapshot and not trigger_snapshot:
            mark_shadow_label(db, shadow_event_id, "unclear")
            if rule_id:
                affected_rule_ids.add(rule_id)
            write_event(
                db, source="shadow_counterfactual",
                outcome="unclear",
                details={"rule_id": rule_id, "shadow_event_id": shadow_event_id, "reason": "empty_snapshot"},
            )
            summary["processed"] += 1
            summary["labeled"] += 1
            continue

        suggestion_text = action_snapshot or trigger_snapshot

        system_prompt = (
            "You are a counterfactual evaluator for a rule memory system. "
            "You will see a rule suggestion and the trigger context where it matched "
            "(but was NOT injected). Judge whether the suggestion WOULD HAVE "
            "helped the assistant if it had been shown.\n\n"
            "You only see the trigger context (what the user asked), not the outcome. "
            "Judge based on whether the context clearly calls for this suggestion.\n\n"
            "Labels:\n"
            "- would_help_high: The context directly involves the scenario this rule addresses, "
            "and missing the suggestion would likely lead to a mistake. "
            "Example: suggestion is 'use --force-with-lease', context is 'force push to main branch'.\n"
            "- would_help_low: The context is related but the suggestion is precautionary — "
            "the assistant could reasonably succeed without it. "
            "Example: suggestion is 'check disk space first', context is 'deploy to production'.\n"
            "- irrelevant: The suggestion topic does not relate to what the user asked.\n"
            "- risky: Applying this suggestion in this context would be wrong or harmful. "
            "Example: suggestion is 'delete cache', but context involves production data.\n"
            "- near_miss: The context shares vocabulary with the trigger but the scenario is different. "
            "Example: suggestion about 'git push --force' but context is about 'docker push'.\n"
            "- unclear: Cannot determine from available information.\n\n"
            "Key distinction: would_help_high = the context is a clear match and omission is risky; "
            "would_help_low = the context is related but the suggestion is just a nice-to-have.\n\n"
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
            response = llm.call(
                system=system_prompt,
                user=user_prompt,
                role="posthoc_evaluator",
            )
        except (json.JSONDecodeError, KeyError, TypeError, RuntimeError,
                OSError, ValueError) as exc:
            log.warning(
                "shadow counterfactual evaluation LLM call failed for event=%s: %s",
                shadow_event_id,
                exc,
            )
            mark_shadow_label(db, shadow_event_id, "unclear")
            if rule_id:
                affected_rule_ids.add(rule_id)
            write_event(
                db, source="shadow_counterfactual",
                outcome="unclear",
                details={"rule_id": rule_id, "shadow_event_id": shadow_event_id, "reason": "llm_failed"},
            )
            summary["failed"] += 1
            summary["processed"] += 1
            continue

        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            data = parse_json_payload(response)
        if not isinstance(data, dict):
            data = None
        if data is None:
            mark_shadow_label(db, shadow_event_id, "unclear")
            if rule_id:
                affected_rule_ids.add(rule_id)
            write_event(
                db, source="shadow_counterfactual",
                outcome="unclear",
                details={"rule_id": rule_id, "shadow_event_id": shadow_event_id, "reason": "parse_failed"},
            )
            summary["labeled"] += 1
            summary["processed"] += 1
            continue
        raw_label = data.get("label", "unclear")
        valid_labels = (
            "would_help_high", "would_help_low", "irrelevant",
            "risky", "near_miss", "unclear",
        )
        label_invalid = raw_label not in valid_labels
        label = "unclear" if label_invalid else raw_label
        mark_shadow_label(db, shadow_event_id, label)
        if rule_id:
            affected_rule_ids.add(rule_id)
        event_details: dict = {
            "rule_id": rule_id,
            "shadow_event_id": shadow_event_id,
        }
        if label_invalid:
            event_details["reason"] = "invalid_label"
        write_event(
            db, source="shadow_counterfactual",
            outcome=label,
            details=event_details,
        )
        summary["labeled"] += 1

        summary["processed"] += 1

    if affected_rule_ids:
        from ..lifecycle.transitions import evaluate_transitions

        for rule_id in sorted(affected_rule_ids):
            result = evaluate_transitions(db, rule_id)
            if result.applied:
                summary["transitions_applied"] += 1

    return summary


def _days_ago_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
