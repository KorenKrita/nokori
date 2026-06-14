"""nokori search — debug retrieval results for a given prompt."""

from __future__ import annotations

import argparse
import os

from ..config import Config
from ..db import fetch_rules, open_db
from ..search.engine import RetrievalEngine
from ..utils.project import resolve_project_id


def run(args: argparse.Namespace, cfg: Config) -> int:
    project_id = args.project
    if project_id is None:
        project_id = resolve_project_id(os.getcwd())

    db = open_db(cfg.db_path)
    try:
        if project_id is None:
            formal_rules = fetch_rules(db, statuses=("active", "trusted"), global_only=True)
        else:
            formal_rules = fetch_rules(db, statuses=("active", "trusted"), project_id=project_id)

        engine = RetrievalEngine(cfg, db)
        result = engine.retrieve(
            args.prompt,
            formal_rules,
            [],
            interaction="cli",
        )

        all_results = result.hot + result.warm
        if not all_results:
            print("(no matching rules)")
            return 0

        # Header
        print(f"{'rank':<5} {'short_id':<10} {'trigger':<62} {'tier':<5} {'score':<8}")
        print("-" * 94)

        for rank, r in enumerate(all_results, start=1):
            trigger_text = (r.rule.trigger_canonical or "")[:60]
            tier = r.level or "warm"
            score = f"{r.rrf_score:.4f}"
            print(f"{rank:<5} {r.rule.short_id:<10} {trigger_text:<62} {tier:<5} {score:<8}")
    finally:
        db.close()

    return 0
