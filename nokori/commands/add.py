from __future__ import annotations

import argparse

from ..config import Config
from ..db import dumps_json, fetch_rule_by_short_id, fetch_short_ids, open_db
from ..errors import NokoriError
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.time import now_iso


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def run(args: argparse.Namespace, cfg: Config) -> int:
    if not args.trigger or not args.action:
        raise NokoriError("--trigger and --action are required")

    now = now_iso()
    rid = new_uuid()

    variants = _split_csv(args.variants)
    terms: dict[str, list[str]] = {}
    if args.terms_en:
        terms["en"] = _split_csv(args.terms_en)
    if args.terms_zh:
        terms["zh"] = _split_csv(args.terms_zh)

    status = "active" if (args.confidence == "high" and args.source_type == "correction") else "candidate"
    project_id = args.project_id
    evidence_score = 3 if (args.confidence == "high" and args.source_type == "correction") else 0
    evidence_log = dumps_json(
        [{"kind": "user_correction", "points": 3, "at": now}]
    ) if evidence_score else "[]"

    db = open_db(cfg.db_path)
    try:
        existing = fetch_short_ids(db)
        sid = short_id_for(rid, existing)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
                "search_terms, behavior, action, rationale, source_type, confidence, "
                "status, evidence_score, evidence_log, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    sid,
                    args.trigger,
                    dumps_json(variants),
                    dumps_json(terms),
                    args.behavior,
                    args.action,
                    args.rationale,
                    args.source_type,
                    args.confidence,
                    status,
                    evidence_score,
                    evidence_log,
                    "project",
                    project_id,
                    now,
                    now,
                ),
            )
        rule = fetch_rule_by_short_id(db, sid)
        if rule:
            index_rule_if_enabled(db, rule, cfg)
    finally:
        db.close()

    print(f"added {sid} ({status})")
    return 0
