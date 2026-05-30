from __future__ import annotations

import argparse
import os

from ..config import Config
from ..db import fetch_rules, fetch_shadow_rules, open_db
from ..search.retrieve import retrieve_and_tier
from ..utils.project import resolve_project_id


def run(args: argparse.Namespace, cfg: Config) -> int:
    project_id = args.project
    if project_id is None:
        project_id = resolve_project_id(os.getcwd())

    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(
            db, statuses=("active", "dormant"), project_id=project_id
        )

        result = retrieve_and_tier(args.prompt, rules, db, cfg, top_k=10)
        hot, warm = result.hot, result.warm

        print(f"prompt        {args.prompt!r}")
        print(f"project_id    {project_id!r}")
        print(f"candidates    {len(rules)} rules in pool")
        print(f"bm25.matches  {result.bm25_matches}")
        print(f"embed.mode    {result.embed_mode}")
        print()
        print(f"HOT  ({len(hot)}):")
        for r in hot:
            cos_str = f"  cos={r.cosine:.3f}" if r.cosine else ""
            print(
                f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  bm25={r.bm25_score:.4f}"
                f"{cos_str}  matched={sorted(r.matched_tokens)}"
            )
            print(f"    {r.rule.trigger_text[:80]}")
        print(f"WARM ({len(warm)}):")
        for r in warm:
            cos_str = f"  cos={r.cosine:.3f}" if r.cosine else ""
            print(
                f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  bm25={r.bm25_score:.4f}"
                f"{cos_str}"
            )

        gateable = [
            r for r in hot if r.rule.confidence == "high" and r.rule.status == "active"
        ]
        print()
        print(f"gate.would_block  {bool(gateable) and cfg.gate_enabled}")
        for r in gateable:
            print(f"  {r.rule.short_id}: {r.rule.action[:80]}")

        if project_id:
            shadow_rules = fetch_shadow_rules(db, project_id=project_id)
            if shadow_rules:
                shadow = retrieve_and_tier(
                    args.prompt, shadow_rules, db, cfg, top_k=5
                )
                if shadow.hot:
                    print()
                    print(
                        f"shadow_pool HOT ({len(shadow.hot)} would record hit, "
                        f"embed={shadow.embed_mode}, not injected):"
                    )
                    for r in shadow.hot[:3]:
                        print(
                            f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  "
                            f"bm25={r.bm25_score:.4f}  proj={r.rule.project_id}"
                        )
    finally:
        db.close()
    return 0
