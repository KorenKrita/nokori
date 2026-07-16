"""Decision application and CAS-style transition writes."""

from __future__ import annotations

from ..db import Db
from ..events.observability import write_event
from ..policy import RUNTIME_POLICY_VERSION
from ..utils.logging import get_logger
from ..utils.time import now_iso
from .transition_types import TransitionDecision, TransitionResult

log = get_logger("nokori.lifecycle.transitions")


def _apply_decision(
    db: Db,
    rule_id: str,
    rule_version: int,
    old_status: str,
    rpv: str | None,
    decision: TransitionDecision,
) -> TransitionResult:
    """Apply a TransitionDecision by calling _apply_transition if needed."""
    if decision.new_status is None:
        return TransitionResult(
            rule_id=rule_id,
            old_status=old_status,
            new_status=None,
            reason=decision.reason,
            applied=False,
        )

    applied = _apply_transition(
        db, rule_id, rule_version, old_status, decision.new_status, rpv, decision.reason
    )
    return TransitionResult(
        rule_id=rule_id,
        old_status=old_status,
        new_status=decision.new_status,
        reason=decision.reason,
        applied=applied,
    )


def _apply_cross_project_promotion(
    db: Db, rule_id: str, rule_version: int, old_status: str,
    *, distinct_count: int | None = None,
) -> TransitionResult:
    """Apply cross-project scope promotion (not a status change)."""
    now = now_iso()
    with db.transaction() as tx:
        cur = tx.execute(
            "UPDATE rules SET project_scope = 'global', "
            "rule_version = rule_version + 1, updated_at = ? "
            "WHERE id = ? AND rule_version = ? AND project_scope = 'project'",
            (now, rule_id, rule_version),
        )
        applied = cur.rowcount == 1
    if applied:
        log.info(
            "cross_project_promotion rule=%s",
            rule_id,
        )
        details: dict = {
            "rule_id": rule_id,
            "transition_type": "cross_project_promotion",
        }
        if distinct_count is not None:
            details["distinct_project_count"] = distinct_count
        write_event(
            db,
            source="lifecycle_transition",
            outcome="cross_project_promotion",
            details=details,
        )
        return TransitionResult(
            rule_id, old_status, None, "cross_project_promotion", True
        )
    log.debug("stale cross_project_promotion rule=%s (CAS failed)", rule_id)
    return TransitionResult(
        rule_id, old_status, None, "cross_project_promotion_conflict", False
    )


# ---------------------------------------------------------------------------
# CAS-style transition application
# ---------------------------------------------------------------------------


def _apply_transition(
    db: Db,
    rule_id: str,
    rule_version: int,
    old_status: str,
    new_status: str,
    runtime_policy_version: str | None,
    reason: str,
) -> bool:
    """CAS update: only succeeds if rule_version and status match expectations.

    Returns True if applied (rowcount=1), False if stale.
    """
    now = now_iso()
    extra_sets = ""
    extra_params: list = []

    if new_status == "suppressed":
        extra_sets = ", suppressed_at = ?"
        extra_params.append(now)
    elif new_status == "trusted":
        extra_sets = ", trusted_at = ?"
        extra_params.append(now)

    if runtime_policy_version is None:
        policy_where = "runtime_policy_version IS NULL"
        policy_params: tuple = ()
    else:
        policy_where = "runtime_policy_version = ?"
        policy_params = (runtime_policy_version,)

    with db.transaction() as tx:
        cur = tx.execute(
            "UPDATE rules SET "
            "status = ?, "
            "rule_version = rule_version + 1, "
            "runtime_policy_version = ?, "
            "updated_at = ?"
            f"{extra_sets} "
            "WHERE id = ? AND rule_version = ? AND status = ? "
            f"AND {policy_where}",
            (
                new_status,
                RUNTIME_POLICY_VERSION,
                now,
                *extra_params,
                rule_id,
                rule_version,
                old_status,
                *policy_params,
            ),
        )
        applied = cur.rowcount == 1

    if applied:
        log.info(
            "transition rule=%s %s->%s reason=%s",
            rule_id,
            old_status,
            new_status,
            reason,
        )
        write_event(
            db,
            source="lifecycle_transition",
            outcome=f"{old_status}_to_{new_status}",
            details={
                "rule_id": rule_id,
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            },
        )
        # System-automated archival creates system-strength negative fingerprint (spec section 11)
        if new_status == "archived":
            _create_system_archive_fingerprint(db, rule_id)
    else:
        log.debug(
            "stale transition rule=%s %s->%s (CAS failed)",
            rule_id,
            old_status,
            new_status,
        )

    return applied


def _create_system_archive_fingerprint(db: Db, rule_id: str) -> None:
    """Create a system-strength archived fingerprint for automated archival."""
    row = db.fetchone(
        "SELECT trigger_canonical, action_instruction, domain_tags FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None:
        return
    from ..archive.fingerprints import create_archived_fingerprint_from_data
    from ..db import loads_json

    domain_tags = loads_json(row["domain_tags"], []) if row["domain_tags"] else []
    create_archived_fingerprint_from_data(
        db,
        rule_id=rule_id,
        trigger_canonical=row["trigger_canonical"] or "",
        action_instruction=row["action_instruction"] or "",
        domain_tags=domain_tags,
        strength="system",
    )

