from __future__ import annotations

import argparse

from ..config import Config
from ..db import fetch_rules, open_db


def run(args: argparse.Namespace, cfg: Config) -> int:
    statuses: tuple[str, ...] | None
    statuses = None if args.all else ("active", "dormant")

    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=statuses, project_id=args.project)
    finally:
        db.close()

    if not rules:
        print("(no rules)")
        return 0

    for r in rules:
        scope = "global" if r.project_scope == "global" else (r.project_id or "-")
        print(
            f"{r.short_id}  {r.status:<9} {r.source_type:<11} {r.confidence:<6} "
            f"hits={r.hit_count:<3} scope={scope}"
        )
        print(f"  trigger: {(r.trigger_text or '')[:60]}")
        print(f"  action : {(r.action or '')[:80]}")
    return 0
