from __future__ import annotations

import json
from dataclasses import dataclass

from ..db import Db, RULE_COLUMNS, dumps_json, fetch_short_ids, row_to_rule
from ..lifecycle.evidence import add_evidence, should_activate_pure_ai
from ..llm.adapter import LLMAdapter
from ..llm.prompts import MERGE_PROMPT
from ..models import Rule
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.logging import get_logger
from ..utils.time import now_iso
from .extractor import Candidate, strip_fence

log = get_logger("nokori.extract.merger")


@dataclass
class MergeOutcome:
    inserted: int
    activated: int
    merged: int
    superseded: int


def _initial_status(cand: Candidate) -> str:
    if cand.confidence == "high" and cand.source_type == "correction":
        return "active"
    return "candidate"


def _persist_new(db: Db, cand: Candidate, project_id: str | None, cfg=None) -> Rule:
    now = now_iso()
    rid = new_uuid()
    sid = short_id_for(rid, fetch_short_ids(db))
    status = _initial_status(cand)
    is_user_correction = (status == "active")
    ev_score = 3 if is_user_correction else 0
    ev_log = dumps_json([{"kind": "user_correction", "points": 3, "at": now}]) if is_user_correction else "[]"
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
            "search_terms, behavior, action, rationale, source_type, confidence, "
            "status, evidence_score, evidence_log, project_scope, project_id, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid, sid, cand.trigger,
                dumps_json(cand.trigger_variants),
                dumps_json(cand.search_terms),
                cand.behavior, cand.action, cand.rationale,
                cand.source_type, cand.confidence, status,
                ev_score, ev_log,
                "project", project_id, now, now,
            ),
        )
    row = db.fetchone(f"SELECT {RULE_COLUMNS} FROM rules WHERE id = ?", (rid,))
    rule = row_to_rule(row)
    if cfg:
        index_rule_if_enabled(db, rule, cfg)
    return rule


def _candidate_neighbors(db: Db, cand: Candidate, limit: int = 5,
                         project_id: str | None = None) -> list[Rule]:
    """Pre-filter by status and project scope, leave semantic match to LLM."""
    if project_id:
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules "
            "WHERE status IN ('candidate','active','dormant') "
            "AND (project_scope = 'global' OR project_id = ? OR project_id IS NULL) "
            "ORDER BY updated_at DESC LIMIT ?",
            (project_id, limit),
        )
    else:
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules "
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
        text = strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("merge LLM returned non-JSON")
        return {"relationships": []}


def _activate(db: Db, rule_id: str, confidence: str, cfg=None) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET status = 'active', confidence = ?, updated_at = ? WHERE id = ?",
            (confidence, now_iso(), rule_id),
        )
    if cfg:
        row = db.fetchone(f"SELECT {RULE_COLUMNS} FROM rules WHERE id = ?", (rule_id,))
        if row:
            index_rule_if_enabled(db, row_to_rule(row), cfg)


def _supersede(db: Db, old_id: str, new_id: str) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET superseded_by = ?, status = 'merged', updated_at = ? "
            "WHERE id = ?",
            (new_id, now_iso(), old_id),
        )


def merge_candidate(
    cand: Candidate,
    db: Db,
    llm: LLMAdapter,
    project_id: str | None = None,
    cfg=None,
) -> MergeOutcome:
    neighbors = _candidate_neighbors(db, cand, project_id=project_id)
    if not neighbors:
        _persist_new(db, cand, project_id, cfg)
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
                    add_evidence(db, existing.id, "user_correction", 3)
                    _activate(db, existing.id, "high", cfg)
                    activated += 1
                else:
                    import dataclasses
                    score, log_list = add_evidence(db, existing.id, "same_extraction", 1)
                    check_rule = dataclasses.replace(
                        existing, evidence_score=score, evidence_log=log_list,
                    )
                    if should_activate_pure_ai(check_rule):
                        _activate(db, existing.id, check_rule.confidence, cfg)
                        activated += 1
        elif verdict == "B":  # BROADER — new supersedes existing
            saw_strong = True
            handled_existing.add(eid)
            new_rule = _persist_new(db, cand, project_id, cfg)
            inserted += 1
            _supersede(db, existing.id, new_rule.id)
            superseded += 1
        elif verdict == "D":  # CONTRADICTS
            saw_strong = True
            handled_existing.add(eid)
            new_rule = _persist_new(db, cand, project_id, cfg)
            inserted += 1
            _supersede(db, existing.id, new_rule.id)
            superseded += 1
        # C (NARROWER) and E (UNRELATED) → coexist; no action.

    if not saw_strong:
        _persist_new(db, cand, project_id, cfg)
        inserted += 1

    if "A" in {(r.get("judgment") or "").upper()[:1] for r in judgment}:
        merged = sum(1 for eid in handled_existing if by_id[eid].status == "candidate")

    return MergeOutcome(inserted=inserted, activated=activated, merged=merged,
                        superseded=superseded)
