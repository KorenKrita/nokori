from __future__ import annotations

import json
from datetime import datetime, timezone

from ..db import Db, dumps_json
from ..lifecycle.evidence import compute_evidence_append
from ..utils.logging import get_logger
from ..utils.time import now_iso

log = get_logger("nokori.lifecycle.promotion")

CROSS_PROJECT_PROMOTE_THRESHOLD = 3


def unique_promotion_project_ids(promotion_evidence: str | list | None) -> list[str]:
    """Distinct other-project ids recorded for global promotion (stable append order)."""
    if promotion_evidence is None:
        raw: list = []
    elif isinstance(promotion_evidence, str):
        raw = json.loads(promotion_evidence or "[]")
    else:
        raw = promotion_evidence
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in raw:
        pid = entry.get("project_id")
        if pid and pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def record_shadow_hit(db: Db, rule_id: str, current_project_id: str | None) -> bool:
    """Returns True if this hit promoted the rule to global."""
    if current_project_id is None:
        return False
    row = db.fetchone(
        "SELECT project_scope, project_id, source_type, promotion_evidence, "
        "evidence_score, evidence_log FROM rules WHERE id = ?",
        (rule_id,),
    )
    if row is None:
        return False
    if row["project_scope"] == "global":
        return False
    if row["project_id"] == current_project_id:
        return False
    if row["source_type"] == "preference":
        return False

    today = datetime.now(timezone.utc).date().isoformat()
    evidence = json.loads(row["promotion_evidence"] or "[]")
    key = f"{current_project_id}:{today}"
    if any(e.get("key") == key for e in evidence):
        return False
    evidence.append({
        "key": key,
        "project_id": current_project_id,
        "date": today,
    })
    unique_projects = set(unique_promotion_project_ids(evidence))

    score, ev_log = compute_evidence_append(
        row["evidence_score"], row["evidence_log"], "shadow_hot", 1
    )
    now = now_iso()

    promoted = False
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET promotion_evidence = ?, "
            "shadow_hit_count = shadow_hit_count + 1, "
            "evidence_score = ?, evidence_log = ?, updated_at = ? "
            "WHERE id = ?",
            (dumps_json(evidence), score, ev_log, now, rule_id),
        )
        if len(unique_projects) >= CROSS_PROJECT_PROMOTE_THRESHOLD:
            tx.execute(
                "UPDATE rules SET project_scope = 'global', updated_at = ? "
                "WHERE id = ?",
                (now, rule_id),
            )
            log.info("rule promoted to global rule=%s", rule_id)
            promoted = True

    return promoted
