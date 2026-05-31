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
    ts = at or now_iso()
    score = (evidence_score or 0) + points
    log_list = loads_json(evidence_log_json, [])
    log_list.append({"kind": kind, "points": points, "at": ts})
    if len(log_list) > MAX_EVIDENCE_LOG_ENTRIES:
        log_list = log_list[-MAX_EVIDENCE_LOG_ENTRIES:]
    return score, dumps_json(log_list)


def add_evidence(db: Db, rule_id: str, kind: str, points: int) -> tuple[int, list[dict]]:
    with db.transaction() as tx:
        row = tx.execute(
            "SELECT evidence_score, evidence_log FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if row is None:
            return (0, [])
        now = now_iso()
        score, log_json = compute_evidence_append(
            row["evidence_score"], row["evidence_log"], kind, points, at=now,
        )
        tx.execute(
            "UPDATE rules SET evidence_score = ?, evidence_log = ?, updated_at = ? "
            "WHERE id = ?",
            (score, log_json, now, rule_id),
        )
    return (score, loads_json(log_json, []))


def evidence_active_days(log_list: list[dict]) -> int:
    dates: set[str] = set()
    for entry in log_list:
        at = entry.get("at") or ""
        if at:
            dates.add(at[:10])
    return len(dates)


def should_activate_pure_ai(rule: Rule) -> bool:
    """For purely AI-derived candidate evidence: score >= 2 and >= 2 active days."""
    if (rule.evidence_score or 0) < 2:
        return False
    return evidence_active_days(rule.evidence_log or []) >= 2
