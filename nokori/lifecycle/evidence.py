"""Evidence scoring for rule lifecycle transitions.

The primary transition logic now lives in :mod:`nokori.lifecycle.transitions`
which aggregates evidence from :mod:`nokori.events.fire` and
:mod:`nokori.events.shadow`. This module retains utility functions used by
the extract merger and for backward compatibility.
"""

from __future__ import annotations

from ..db import Db, dumps_json, loads_json
from ..models import Rule
from ..utils.time import now_iso

MAX_EVIDENCE_LOG_ENTRIES = 50


def compute_evidence_append(
    evidence_score: int | None,
    evidence_log_json: str | None,
    kind: str,
    points: int,
    *,
    at: str | None = None,
) -> tuple[int, str]:
    """Append an evidence entry and return (new_score, log_json).

    Retained for use by extract/merger.py which records evidence during
    rule extraction before the flywheel takes over.
    """
    ts = at or now_iso()
    score = (evidence_score or 0) + points
    log_list = loads_json(evidence_log_json, [])
    log_list.append({"kind": kind, "points": points, "at": ts})
    if len(log_list) > MAX_EVIDENCE_LOG_ENTRIES:
        log_list = log_list[-MAX_EVIDENCE_LOG_ENTRIES:]
    return score, dumps_json(log_list)


def add_evidence(db: Db, rule_id: str, kind: str, points: int) -> tuple[float, list[dict]]:
    """Add evidence points to a rule and persist.

    Used by extract/merger.py for initial evidence at extraction time.
    Maps to the evidence_support_score field (float).
    """
    with db.transaction() as tx:
        row = tx.execute(
            "SELECT evidence_support_score FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if row is None:
            return (0.0, [])
        now = now_iso()
        new_score = float(row["evidence_support_score"] or 0.0) + points
        tx.execute(
            "UPDATE rules SET evidence_support_score = ?, updated_at = ? "
            "WHERE id = ?",
            (new_score, now, rule_id),
        )
    return (new_score, [])


def evidence_active_days(log_list: list[dict]) -> int:
    """Count distinct calendar days with evidence entries."""
    dates: set[str] = set()
    for entry in log_list:
        at = entry.get("at") or ""
        if at:
            dates.add(at[:10])
    return len(dates)


def should_activate_pure_ai(rule: Rule) -> bool:
    """For purely AI-derived candidate evidence: score >= 2.0.

    .. deprecated::
        Activation is now handled by lifecycle.transitions via shadow evidence
        aggregation. Retained for backward compat with extract/merger.py.
    """
    return (rule.evidence_support_score or 0.0) >= 2.0
