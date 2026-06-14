from __future__ import annotations

import argparse
import json

from ..config import Config
from ..db import fetch_rules, open_db


def run(args: argparse.Namespace, cfg: Config) -> int:
    if getattr(args, "global_eligible", False):
        if getattr(args, "all", False) or getattr(args, "project", None):
            print("error: --global-eligible cannot be combined with --all or --project")
            return 1
        if getattr(args, "json", False):
            print("error: --json is not supported with --global-eligible")
            return 1
        return _run_global_eligible(cfg)

    statuses: tuple[str, ...] | None
    statuses = None if args.all else ("active", "trusted", "candidate")

    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=statuses, project_id=args.project)
    finally:
        db.close()

    if getattr(args, "json", False):
        rules_list = [
            {
                "short_id": r.short_id,
                "status": r.status,
                "trigger": (r.trigger_canonical or "")[:100],
                "severity": r.severity,
                "project_id": r.project_id,
                "created_at": r.created_at,
            }
            for r in rules
        ]
        print(json.dumps(rules_list, ensure_ascii=False))
        return 0

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


def _run_global_eligible(cfg: Config) -> int:
    """Show trusted project-scoped rules approaching cross-project promotion."""
    from ..events.fire import batch_count_distinct_useful_projects
    from ..policy import CROSS_PROJECT_PROMOTION_THRESHOLD

    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=("trusted",))
        project_rules = [r for r in rules if r.project_scope == "project"]
        counts = batch_count_distinct_useful_projects(db, [r.id for r in project_rules])
        # ponytail: show rules within 1 step of promotion (at least 2 distinct projects)
        approaching = max(2, CROSS_PROJECT_PROMOTION_THRESHOLD - 1)
        eligible = [
            (r, counts.get(r.id, 0))
            for r in project_rules
            if counts.get(r.id, 0) >= approaching
        ]
    finally:
        db.close()

    if not eligible:
        print("(no rules approaching cross-project promotion)")
        return 0

    target = CROSS_PROJECT_PROMOTION_THRESHOLD
    for r, count in eligible:
        print(
            f"{r.short_id}  projects={count}/{target}  "
            f"trigger: {(r.trigger_canonical or '')[:50]}"
        )
    return 0
