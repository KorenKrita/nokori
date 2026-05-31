from __future__ import annotations

import argparse

from ..config import Config
from ..db import dumps_json, fetch_rule_by_short_id, open_db
from ..errors import NokoriError
from ..search.embedding import index_rule_if_enabled
from ..utils.text import split_csv
from ..utils.time import now_iso

_EDITABLE_COLUMNS = frozenset({
    "trigger_text", "action", "rationale", "confidence", "status",
    "archived_reason", "superseded_by", "trigger_variants", "search_terms",
})


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, args.short_id)
        if rule is None:
            raise NokoriError(f"no rule with short_id {args.short_id!r}")

        updates: list[tuple[str, str | int]] = []
        if args.trigger is not None:
            updates.append(("trigger_text", args.trigger))
        if args.action is not None:
            updates.append(("action", args.action))
        if args.rationale is not None:
            updates.append(("rationale", args.rationale))
        if args.confidence is not None:
            updates.append(("confidence", args.confidence))
        if args.status is not None:
            new_status = args.status
            allowed = {
                "candidate": {"active", "archived"},
                "active": {"dormant", "archived"},
                "dormant": {"active", "archived"},
                "merged": {"archived"},
                "archived": set(),
            }
            if new_status not in allowed.get(rule.status, set()):
                raise NokoriError(
                    f"invalid status transition {rule.status!r} -> {new_status!r}"
                )
            updates.append(("status", new_status))
            if new_status == "active":
                updates.append(("archived_reason", None))
                updates.append(("superseded_by", None))
            elif new_status == "archived":
                updates.append(("archived_reason", "manual_edit"))
        if args.variants is not None:
            updates.append(("trigger_variants", dumps_json(split_csv(args.variants))))
        if args.terms_en is not None or args.terms_zh is not None:
            terms = dict(rule.search_terms)
            if args.terms_en is not None:
                terms["en"] = split_csv(args.terms_en)
            if args.terms_zh is not None:
                terms["zh"] = split_csv(args.terms_zh)
            updates.append(("search_terms", dumps_json(terms)))

        if not updates:
            print("nothing to update")
            return 0

        now = now_iso()
        for col, _ in updates:
            if col not in _EDITABLE_COLUMNS:
                raise NokoriError(f"internal error: disallowed column {col!r}")
        sets = ", ".join(f"{col} = ?" for col, _ in updates)
        params: list = [val for _, val in updates]
        params.extend([now, rule.id])
        with db.transaction() as tx:
            tx.execute(
                f"UPDATE rules SET {sets}, updated_at = ? WHERE id = ?",
                tuple(params),
            )
        print(f"updated {rule.short_id}: {', '.join(c for c, _ in updates)}")
        reindex_cols = {
            "trigger_text", "trigger_variants", "search_terms",
            "action", "rationale",
        }
        if reindex_cols & {col for col, _ in updates}:
            updated_rule = fetch_rule_by_short_id(db, args.short_id)
            if updated_rule and updated_rule.status not in ("archived", "merged"):
                index_rule_if_enabled(db, updated_rule, cfg)
    finally:
        db.close()
    return 0
