"""State transition engine for the autonomous rule quality flywheel.

Evaluates evidence and applies policy-driven status transitions using
CAS-style updates to prevent stale-state races.
"""

from __future__ import annotations

from ..db import Db
from ..policy import Status
from .transition_apply import _apply_cross_project_promotion, _apply_decision
from .transition_barriers import compute_promotion_barriers
from .transition_batch import run_all_pending_transitions
from .transition_evaluate import (
    _evaluate_active,
    _evaluate_candidate,
    _evaluate_suppressed,
    _evaluate_trusted,
)
from .transition_gather import (
    _gather_active_evidence,
    _gather_candidate_evidence,
    _gather_suppressed_evidence,
    _gather_trusted_evidence,
    update_derived_scores,
)
from .transition_types import EvidenceSnapshot, TransitionDecision, TransitionResult


def evaluate_transitions(db: Db, rule_id: str) -> TransitionResult:
    """Main entry point. Reads rule state, aggregates events, applies policy."""
    row = db.fetchone(
        "SELECT id, rule_version, status, runtime_policy_version, "
        "suppressed_at, replacement_id, project_scope "
        "FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None:
        return TransitionResult(
            rule_id=rule_id,
            old_status="",
            new_status=None,
            reason="rule not found",
            applied=False,
        )

    status: Status = row["status"]
    rule_version: int = row["rule_version"]
    rpv = row["runtime_policy_version"]

    if status == "candidate":
        evidence = _gather_candidate_evidence(db, row, rule_version)
        decision = _evaluate_candidate(evidence)
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)
    if status == "active":
        evidence = _gather_active_evidence(db, row, rule_version)
        decision = _evaluate_active(evidence)
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)
    if status == "trusted":
        evidence = _gather_trusted_evidence(db, row, rule_version)
        decision = _evaluate_trusted(evidence)
        if decision.reason == "cross_project_promotion":
            return _apply_cross_project_promotion(
                db, rule_id, rule_version, status,
                distinct_count=decision.metadata.get("distinct_project_count"),
            )
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)
    if status == "suppressed":
        evidence = _gather_suppressed_evidence(db, row, rule_version)
        decision = _evaluate_suppressed(evidence)
        return _apply_decision(db, rule_id, rule_version, status, rpv, decision)

    # archived rules do not transition
    return TransitionResult(
        rule_id=rule_id,
        old_status=status,
        new_status=None,
        reason="terminal state",
        applied=False,
    )


__all__ = [
    "EvidenceSnapshot",
    "TransitionDecision",
    "TransitionResult",
    "compute_promotion_barriers",
    "evaluate_transitions",
    "run_all_pending_transitions",
    "update_derived_scores",
    "_evaluate_active",
    "_evaluate_candidate",
    "_evaluate_suppressed",
    "_evaluate_trusted",
]
