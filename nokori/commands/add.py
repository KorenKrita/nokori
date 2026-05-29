from __future__ import annotations

import argparse
from datetime import datetime, timezone

from ..config import Config
from ..db import dumps_json, fetch_short_ids, open_db
from ..errors import NokoriError
from ..utils.ids import new_uuid, short_id_for


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def run(args: argparse.Namespace, cfg: Config) -> int:
    if not args.trigger or not args.action:
        raise NokoriError("--trigger and --action are required")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    rid = new_uuid()

    variants = _split_csv(args.variants)
    terms: dict[str, list[str]] = {}
    if args.terms_en:
        terms["en"] = _split_csv(args.terms_en)
    if args.terms_zh:
        terms["zh"] = _split_csv(args.terms_zh)

    status = "active" if args.confidence == "high" else "candidate"

    db = open_db(cfg.db_path)
    try:
        existing = fetch_short_ids(db)
        sid = short_id_for(rid, existing)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
                "search_terms, behavior, action, rationale, source_type, confidence, "
                "status, project_scope, project_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    "project",
                    args.project_id,
                    now,
                    now,
                ),
            )
            tx.execute(
                "INSERT INTO rule_terms (rule_id, lang, term, term_type) VALUES (?,?,?,?)",
                (rid, "en", args.trigger, "trigger"),
            )
            for term in variants:
                tx.execute(
                    "INSERT INTO rule_terms (rule_id, lang, term, term_type) VALUES (?,?,?,?)",
                    (rid, "en", term, "variant"),
                )
            for lang, items in terms.items():
                for term in items:
                    tx.execute(
                        "INSERT INTO rule_terms (rule_id, lang, term, term_type) VALUES (?,?,?,?)",
                        (rid, lang, term, "search"),
                    )
    finally:
        db.close()

    print(f"added {sid} ({status})")
    return 0
