from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

from ..db import Db, RULE_COLUMNS, dumps_json, fetch_short_ids, row_to_rule
from ..lifecycle.evidence import add_evidence, should_activate_pure_ai
from ..llm.adapter import LLMAdapter
from ..llm.prompts import MERGE_SYSTEM, format_merge_user
from ..models import Rule
from ..search import bm25
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.logging import get_logger
from ..utils.time import now_iso
from .extractor import Candidate, strip_fence

log = get_logger("nokori.extract.merger")

# Cold-path merge: BM25 pre-filter before LLM relationship judgment.
MERGE_NEIGHBOR_LIMIT = 20
MERGE_RECENT_FALLBACK = 5

# LLM may return single letters or full words (CONTRADICTS not C).
_VERDICT_ALIASES: dict[str, str] = {
    "A": "A",
    "SAME": "A",
    "B": "B",
    "BROADER": "B",
    "C": "C",
    "NARROWER": "C",
    "D": "D",
    "CONTRADICTS": "D",
    "E": "E",
    "UNRELATED": "E",
}


def _normalize_merge_verdict(raw: str) -> str | None:
    token = (raw or "").strip().upper().split()[0].strip(".,;:\"'") if (raw or "").strip() else ""
    if not token:
        return None
    if token in _VERDICT_ALIASES:
        return _VERDICT_ALIASES[token]
    if len(token) == 1 and token in "ABCDE":
        return token
    return None


@dataclass
class MergeOutcome:
    inserted: int
    activated: int
    merged: int
    superseded: int
    merge_ok: bool = True


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
    if project_id:
        scope, pid = "project", project_id
    else:
        scope, pid = "global", None
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
                scope, pid, now, now,
            ),
        )
    row = db.fetchone(f"SELECT {RULE_COLUMNS} FROM rules WHERE id = ?", (rid,))
    if row is None:
        raise RuntimeError("inserted rule missing after persist")
    rule = row_to_rule(row)
    if cfg:
        index_rule_if_enabled(db, rule, cfg)
    return rule


def _candidate_query(cand: Candidate) -> str:
    parts = [cand.trigger, cand.action]
    if cand.behavior:
        parts.append(cand.behavior)
    parts.extend(cand.trigger_variants)
    for terms in cand.search_terms.values():
        parts.extend(terms)
    return " ".join(p for p in parts if p)


def _fetch_merge_pool(db: Db, project_id: str | None) -> list[Rule]:
    """All rules eligible for merge comparison in this project scope."""
    if project_id:
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules "
            "WHERE status IN ('candidate','active','dormant') "
            "AND (project_scope = 'global' OR project_id = ?) "
            "ORDER BY updated_at DESC",
            (project_id,),
        )
    else:
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules "
            "WHERE status IN ('candidate','active','dormant') "
            "AND project_scope = 'global' "
            "ORDER BY updated_at DESC",
        )
    return [row_to_rule(r) for r in rows]


def _recent_neighbors(
    db: Db, project_id: str | None, *, limit: int
) -> list[Rule]:
    if project_id:
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules "
            "WHERE status IN ('candidate','active','dormant') "
            "AND (project_scope = 'global' OR project_id = ?) "
            "ORDER BY updated_at DESC LIMIT ?",
            (project_id, limit),
        )
    else:
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules "
            "WHERE status IN ('candidate','active','dormant') "
            "AND project_scope = 'global' "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
    return [row_to_rule(r) for r in rows]


def _candidate_neighbors(
    db: Db,
    cand: Candidate,
    *,
    project_id: str | None = None,
    limit: int = MERGE_NEIGHBOR_LIMIT,
) -> list[Rule]:
    """BM25 pre-filter top-N in pool; backfill with recent rules if overlap is thin."""
    pool = _fetch_merge_pool(db, project_id)
    if not pool:
        return []

    query = _candidate_query(cand)
    ranked = bm25.search(query, pool, top_k=limit)
    neighbors: list[Rule] = [r.rule for r in ranked]

    if len(neighbors) >= MERGE_RECENT_FALLBACK:
        return neighbors

    seen = {r.id for r in neighbors}
    for r in _recent_neighbors(db, project_id, limit=MERGE_RECENT_FALLBACK):
        if r.id in seen:
            continue
        neighbors.append(r)
        seen.add(r.id)
        if len(neighbors) >= limit:
            break
    return neighbors


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


def _ask_llm(cand: Candidate, neighbors: list[Rule], llm: LLMAdapter) -> dict | None:
    user_content = format_merge_user(
        trigger=cand.trigger,
        action=cand.action,
        source_type=cand.source_type,
        confidence=cand.confidence,
        existing_formatted=_format_existing(neighbors),
    )
    try:
        raw = llm.complete_messages(
            MERGE_SYSTEM, user_content, max_tokens=1500, timeout=45,
        )
    except Exception as e:
        log.warning("merge LLM failed: %s", type(e).__name__)
        return None
    if raw is None:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = strip_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("merge LLM returned non-JSON")
        return None
    if isinstance(data, list):
        return {"relationships": data}
    if isinstance(data, dict):
        return data
    return None


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

    judgment_payload = _ask_llm(cand, neighbors, llm)
    if judgment_payload is None:
        log.warning(
            "merge llm failed, keeping extract pending (neighbors exist): %s",
            cand.trigger[:60],
        )
        return MergeOutcome(
            inserted=0, activated=0, merged=0, superseded=0, merge_ok=False,
        )
    judgment = judgment_payload.get("relationships") or []
    by_id = {r.id: r for r in neighbors}
    inserted = activated = merged = superseded = 0
    handled_existing: set[str] = set()
    saw_strong = False
    pending_new: Rule | None = None
    anchor_id: str | None = None

    parsed: list[tuple[str, str, Rule]] = []
    seen_eids: set[str] = set()
    for rel in judgment:
        if not isinstance(rel, dict):
            continue
        eid = rel.get("existing_id")
        verdict = _normalize_merge_verdict(rel.get("judgment") or "")
        if not eid or eid not in by_id or verdict is None:
            continue
        if eid in seen_eids:
            continue
        seen_eids.add(eid)
        parsed.append((eid, verdict, by_id[eid]))

    # Pass 1: SAME (A) — establishes anchor before BROADER/CONTRADICTS (B/D).
    for eid, verdict, existing in parsed:
        if verdict != "A":
            continue
        saw_strong = True
        handled_existing.add(eid)
        if anchor_id is None:
            anchor_id = existing.id
        merged += 1
        if existing.status == "candidate":
            if cand.confidence == "high" and cand.source_type == "correction":
                add_evidence(db, existing.id, "user_correction", 3)
                _activate(db, existing.id, "high", cfg)
                activated += 1
            else:
                score, log_list = add_evidence(db, existing.id, "same_extraction", 1)
                check_rule = dataclasses.replace(
                    existing, evidence_score=score, evidence_log=log_list,
                )
                if should_activate_pure_ai(check_rule):
                    _activate(db, existing.id, check_rule.confidence, cfg)
                    activated += 1
        elif existing.status in ("active", "dormant"):
            add_evidence(db, existing.id, "same_extraction", 1)

    # Pass 2: B/D — may supersede onto anchor_id from pass 1 regardless of LLM order.
    for eid, verdict, existing in parsed:
        if verdict not in ("B", "D"):
            continue
        if eid in handled_existing:
            continue
        saw_strong = True
        handled_existing.add(eid)
        if pending_new is not None:
            winner_id = pending_new.id
        elif anchor_id is not None:
            winner_id = anchor_id
        else:
            pending_new = _persist_new(db, cand, project_id, cfg)
            inserted += 1
            winner_id = pending_new.id
        _supersede(db, existing.id, winner_id)
        superseded += 1

    if not saw_strong:
        _persist_new(db, cand, project_id, cfg)
        inserted += 1

    return MergeOutcome(
        inserted=inserted,
        activated=activated,
        merged=merged,
        superseded=superseded,
        merge_ok=True,
    )
