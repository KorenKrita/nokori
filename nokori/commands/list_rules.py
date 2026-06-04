from __future__ import annotations

import argparse

from ..config import Config
from ..db import fetch_rules, open_db


def run(args: argparse.Namespace, cfg: Config) -> int:
    statuses: tuple[str, ...] | None
    statuses = None if args.all else ("active", "trusted", "candidate")

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
            f"{r.short_id}  {r.status:<11} {r.severity:<14} "
            f"useful={r.observed_usefulness_score:.2f}  "
            f"fp={r.false_positive_score:.2f}  "
            f"harmful={r.harmful_score:.2f}  "
            f"scope={scope}"
        )
        print(f"  trigger: {(r.trigger_canonical or '')[:60]}")
        print(f"  action : {(r.action_instruction or '')[:80]}")
    return 0
