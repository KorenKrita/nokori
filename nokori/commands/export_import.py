from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import Config
from ..db import (
    dumps_json,
    fetch_rule_by_short_id,
    fetch_rules,
    fetch_short_ids,
    open_db,
)
from ..errors import NokoriError
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.time import now_iso


def run_export(args: argparse.Namespace, cfg: Config) -> int:
    target = Path(args.path).expanduser().resolve()
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=None)
    finally:
        db.close()

    payload = {
        "format": "nokori-export",
        "version": 1,
        "exported_at": now_iso(),
        "rules": [
            {
                "id": r.id,
                "short_id": r.short_id,
                "trigger_text": r.trigger_text,
                "trigger_variants": r.trigger_variants,
                "search_terms": r.search_terms,
                "behavior": r.behavior,
                "action": r.action,
                "rationale": r.rationale,
                "source_type": r.source_type,
                "confidence": r.confidence,
                "status": r.status,
                "evidence_score": r.evidence_score,
                "evidence_log": r.evidence_log,
                "hit_count": r.hit_count,
                "last_hit": r.last_hit,
                "cross_project_hits": r.cross_project_hits,
                "promotion_evidence": r.promotion_evidence,
                "project_scope": r.project_scope,
                "project_id": r.project_id,
                "merged_from": r.merged_from,
                "merged_into": r.merged_into,
                "superseded_by": r.superseded_by,
                "archived_reason": r.archived_reason,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rules
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported {len(rules)} rules → {target}")
    return 0


def run_import(args: argparse.Namespace, cfg: Config) -> int:
    src = Path(args.path).expanduser().resolve()
    if not src.exists():
        raise NokoriError(f"file not found: {src}")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NokoriError(f"invalid JSON: {e}") from e
    if data.get("format") != "nokori-export":
        raise NokoriError("unrecognized export format")
    rules_in = data.get("rules") or []

    db = open_db(cfg.db_path)
    inserted = skipped = 0
    inserted_sids: list[str] = []
    try:
        existing_ids = {r["id"] for r in db.fetchall("SELECT id FROM rules")}
        existing_short_ids = fetch_short_ids(db)
        for rec in rules_in:
            if rec.get("id") in existing_ids:
                skipped += 1
                continue
            rid = rec.get("id") or new_uuid()
            sid = rec.get("short_id") or short_id_for(rid, existing_short_ids)
            if sid in existing_short_ids:
                sid = short_id_for(new_uuid(), existing_short_ids)
            existing_short_ids.add(sid)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
                    "search_terms, behavior, action, rationale, source_type, confidence, "
                    "status, evidence_score, evidence_log, hit_count, last_hit, "
                    "cross_project_hits, promotion_evidence, project_scope, project_id, "
                    "merged_from, merged_into, superseded_by, archived_reason, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rid, sid,
                        rec.get("trigger_text", ""),
                        dumps_json(rec.get("trigger_variants") or []),
                        dumps_json(rec.get("search_terms") or {}),
                        rec.get("behavior"),
                        rec.get("action", ""),
                        rec.get("rationale"),
                        rec.get("source_type", "correction"),
                        rec.get("confidence", "medium"),
                        rec.get("status", "candidate"),
                        rec.get("evidence_score", 0),
                        dumps_json(rec.get("evidence_log") or []),
                        rec.get("hit_count", 0),
                        rec.get("last_hit"),
                        rec.get("cross_project_hits", 0),
                        dumps_json(rec.get("promotion_evidence") or []),
                        rec.get("project_scope", "project"),
                        rec.get("project_id"),
                        dumps_json(rec.get("merged_from") or []),
                        rec.get("merged_into"),
                        rec.get("superseded_by"),
                        rec.get("archived_reason"),
                        rec.get("created_at") or now_iso(),
                        rec.get("updated_at") or now_iso(),
                    ),
                )
            inserted += 1
            inserted_sids.append(sid)
        for sid in inserted_sids:
            rule = fetch_rule_by_short_id(db, sid)
            if rule and rule.status in ("active", "dormant"):
                index_rule_if_enabled(db, rule, cfg)
    finally:
        db.close()
    print(f"imported {inserted} rules; skipped {skipped} (already present)")
    return 0
