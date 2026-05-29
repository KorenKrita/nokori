from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..db import Db, dumps_json, fetch_short_ids, row_to_rule
from ..llm.adapter import LLMAdapter
from ..llm.prompts import MERGE_PROMPT
from ..models import Rule
from ..utils.ids import new_uuid, short_id_for
from ..utils.logging import get_logger
from .extractor import Candidate

log = get_logger("nokori.extract.merger")

_RULE_COLUMNS = (
    "id, short_id, trigger_text, trigger_variants, search_terms, behavior, action, "
    "rationale, source_type, confidence, status, evidence_score, evidence_log, "
    "hit_count, last_hit, cross_project_hits, promotion_evidence, project_scope, "
    "project_id, merged_from, merged_into, superseded_by, archived_reason, "
    "created_at, updated_at"
)


@dataclass
class MergeOutcome:
    inserted: int
    activated: int
    merged: int
    superseded: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _initial_status(cand: Candidate) -> str:
    if cand.confidence == "high" and cand.source_type == "correction":
        return "active"
    return "candidate"


def _persist_new(db: Db, cand: Candidate, project_id: str | None) -> Rule:
    now = _now_iso()
    rid = new_uuid()
    sid = short_id_for(rid, fetch_short_ids(db))
    status = _initial_status(cand)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
            "search_terms, behavior, action, rationale, source_type, confidence, "
            "status, project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid, sid, cand.trigger,
                dumps_json(cand.trigger_variants),
                dumps_json(cand.search_terms),
                cand.behavior, cand.action, cand.rationale,
                cand.source_type, cand.confidence, status,
                "project", project_id, now, now,
            ),
        )
        tx.execute(
            "INSERT INTO rule_terms (rule_id, lang, term, term_type) VALUES (?,?,?,?)",
            (rid, "en", cand.trigger, "trigger"),
        )
        for v in cand.trigger_variants:
            tx.execute(
                "INSERT INTO rule_terms (rule_id, lang, term, term_type) VALUES (?,?,?,?)",
                (rid, "en", v, "variant"),
            )
        for lang, items in cand.search_terms.items():
            for term in items:
                tx.execute(
                    "INSERT INTO rule_terms (rule_id, lang, term, term_type) VALUES (?,?,?,?)",
                    (rid, lang, term, "search"),
                )
    row = db.fetchone(f"SELECT {_RULE_COLUMNS} FROM rules WHERE id = ?", (rid,))
    return row_to_rule(row)


def _candidate_neighbors(db: Db, cand: Candidate, limit: int = 5) -> list[Rule]:
    """Cheap candidate set: pre-filter by status, leave semantic match to LLM."""
    rows = db.fetchall(
        f"SELECT {_RULE_COLUMNS} FROM rules "
        "WHERE status IN ('candidate','active','dormant') "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    return [row_to_rule(r) for r in rows]


def _format_existing(rules: list[Rule]) -> str:
    parts = []
    for r in rules:
        parts.append(
            f"- id={r.id}\n  trigger: {r.trigger_text}\n"
            f"  action: {r.action}\n"
            f"  source_type: {r.source_type}\n  confidence: {r.confidence}\n"
            f"  status: {r.status}"
        )
    return "\n".join(parts)


def _ask_llm(cand: Candidate, neighbors: list[Rule], llm: LLMAdapter) -> dict:
    prompt = (MERGE_PROMPT
              .replace("{trigger}", cand.trigger)
              .replace("{action}", cand.action)
              .replace("{source_type}", cand.source_type)
              .replace("{confidence}", cand.confidence)
              .replace("{existing_formatted}", _format_existing(neighbors)))
    try:
        raw = llm.complete(prompt, max_tokens=1500, timeout=45)
    except Exception as e:
        log.warning("merge LLM failed: %s", type(e).__name__)
        return {"relationships": []}
    if raw is None:
        return {"relationships": []}
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("merge LLM returned non-JSON")
        return {"relationships": []}


def _activate(db: Db, rule_id: str, confidence: str) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET status = 'active', confidence = ?, updated_at = ? WHERE id = ?",
            (confidence, _now_iso(), rule_id),
        )


def _add_evidence(db: Db, rule_id: str, kind: str, points: int) -> None:
    row = db.fetchone(
        "SELECT evidence_score, evidence_log FROM rules WHERE id = ?", (rule_id,)
    )
    if row is None:
        return
    score = (row["evidence_score"] or 0) + points
    log_list = json.loads(row["evidence_log"] or "[]")
    log_list.append({"kind": kind, "points": points, "at": _now_iso()})
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET evidence_score = ?, evidence_log = ?, updated_at = ? "
            "WHERE id = ?",
            (score, dumps_json(log_list), _now_iso(), rule_id),
        )


def _supersede(db: Db, old_id: str, new_id: str) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET superseded_by = ?, status = 'merged', updated_at = ? "
            "WHERE id = ?",
            (new_id, _now_iso(), old_id),
        )


def _link_merge(db: Db, old_id: str, new_id: str) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET merged_into = ?, status = 'merged', updated_at = ? "
            "WHERE id = ?",
            (new_id, _now_iso(), old_id),
        )


def merge_candidate(
    cand: Candidate,
    db: Db,
    llm: LLMAdapter,
    project_id: str | None = None,
) -> MergeOutcome:
    neighbors = _candidate_neighbors(db, cand)
    if not neighbors:
        _persist_new(db, cand, project_id)
        return MergeOutcome(inserted=1, activated=0, merged=0, superseded=0)

    judgment = _ask_llm(cand, neighbors, llm).get("relationships", [])
    by_id = {r.id: r for r in neighbors}
    inserted = activated = merged = superseded = 0
    handled_existing: set[str] = set()
    saw_strong = False

    for rel in judgment:
        eid = rel.get("existing_id")
        verdict = (rel.get("judgment") or "").strip().upper()[:1]
        if not eid or eid not in by_id or verdict not in {"A", "B", "C", "D", "E"}:
            continue
        existing = by_id[eid]
        if verdict == "A":  # SAME
            saw_strong = True
            handled_existing.add(eid)
            if existing.status == "candidate":
                if cand.confidence == "high" and cand.source_type == "correction":
                    _activate(db, existing.id, "high")
                    activated += 1
                else:
                    _add_evidence(db, existing.id, "same_extraction", 1)
        elif verdict == "B":  # BROADER — new supersedes existing
            saw_strong = True
            handled_existing.add(eid)
            new_rule = _persist_new(db, cand, project_id)
            inserted += 1
            _supersede(db, existing.id, new_rule.id)
            superseded += 1
        elif verdict == "D":  # CONTRADICTS
            saw_strong = True
            handled_existing.add(eid)
            new_rule = _persist_new(db, cand, project_id)
            inserted += 1
            _supersede(db, existing.id, new_rule.id)
            superseded += 1
        # C (NARROWER) and E (UNRELATED) → coexist; no action.

    if not saw_strong:
        _persist_new(db, cand, project_id)
        inserted += 1

    if "A" in {(r.get("judgment") or "").upper()[:1] for r in judgment}:
        merged = sum(1 for eid in handled_existing if by_id[eid].status == "candidate")

    return MergeOutcome(inserted=inserted, activated=activated, merged=merged,
                        superseded=superseded)
