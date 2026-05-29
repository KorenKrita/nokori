from __future__ import annotations

import argparse

from ..config import Config
from ..db import fetch_rules, open_db
from ..search import bm25, ranker


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(
            db, statuses=("active", "dormant"), project_id=args.project
        )
    finally:
        db.close()

    bm25_results = bm25.search(args.prompt, rules, top_k=10)
    fused = ranker.rrf_fuse(bm25_results, [])
    hot, warm = ranker.tier_results(fused)

    print(f"prompt        {args.prompt!r}")
    print(f"candidates    {len(rules)} rules in pool")
    print(f"bm25.matches  {len(bm25_results)}")
    print()
    print(f"HOT  ({len(hot)}):")
    for r in hot:
        print(
            f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  bm25={r.bm25_score:.4f}  "
            f"matched={sorted(r.matched_tokens)}"
        )
        print(f"    {r.rule.trigger_text[:80]}")
    print(f"WARM ({len(warm)}):")
    for r in warm:
        print(
            f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  bm25={r.bm25_score:.4f}"
        )

    gateable = [
        r for r in hot if r.rule.confidence == "high" and r.rule.status == "active"
    ]
    print()
    print(f"gate.would_block  {bool(gateable) and cfg.gate_enabled}")
    for r in gateable:
        print(f"  {r.rule.short_id}: {r.rule.action[:80]}")
    return 0
