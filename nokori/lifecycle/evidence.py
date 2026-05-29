from __future__ import annotations

import json
from datetime import datetime, timezone

from ..db import Db, dumps_json
from ..models import Rule


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def add_evidence(db: Db, rule_id: str, kind: str, points: int) -> tuple[int, list[dict]]:
    row = db.fetchone(
        "SELECT evidence_score, evidence_log FROM rules WHERE id = ?", (rule_id,)
    )
    if row is None:
        return (0, [])
    score = (row["evidence_score"] or 0) + points
    log_list = json.loads(row["evidence_log"] or "[]")
    log_list.append({"kind": kind, "points": points, "at": _now_iso()})
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET evidence_score = ?, evidence_log = ?, updated_at = ? "
            "WHERE id = ?",
            (score, dumps_json(log_list), _now_iso(), rule_id),
        )
    return (score, log_list)


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
