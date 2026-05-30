from __future__ import annotations

import argparse
import os

from ..config import Config
from ..db import fetch_rules, fetch_shadow_rules, open_db
from ..gate.blocker import select_gate_rules
from ..search.retrieve import retrieve_formal_and_shadow
from ..utils.project import resolve_project_id


def run(args: argparse.Namespace, cfg: Config) -> int:
    project_id = args.project
    if project_id is None:
        project_id = resolve_project_id(os.getcwd())

    db = open_db(cfg.db_path)
    try:
        if project_id is None:
            formal_rules = fetch_rules(
                db, statuses=("active", "dormant"), global_only=True
            )
        else:
            formal_rules = fetch_rules(
                db, statuses=("active", "dormant"), project_id=project_id
            )
        shadow_rules = (
            fetch_shadow_rules(db, project_id=project_id)
            if project_id and cfg.promotion_enabled
            else []
        )
        result, shadow_hot = retrieve_formal_and_shadow(
            args.prompt,
            formal_rules,
            shadow_rules,
            db,
            cfg,
            interaction="cli",
        )
        hot, warm = result.hot, result.warm

        print(f"prompt        {args.prompt!r}")
        print(f"project_id    {project_id!r}")
        print(f"formal.pool   {len(formal_rules)} rules")
        print(f"shadow.pool   {len(shadow_rules)} rules")
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
            if getattr(r, "retrieval_hot", False) and r.rule.status == "dormant":
                print("    (dormant: injected as WARM; DB reactivated for next turn)")

        gateable = select_gate_rules(hot)
        print()
        print(f"gate.would_block  {bool(gateable) and cfg.gate_enabled}")
        for r in gateable:
            print(f"  {r.rule.short_id}: {r.rule.action[:80]}")

        if shadow_hot:
            print()
            print(
                f"shadow_pool HOT ({len(shadow_hot)} would record hit, "
                f"embed={result.embed_mode}, not injected):"
            )
            for r in shadow_hot[:3]:
                print(
                    f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  "
                    f"bm25={r.bm25_score:.4f}  proj={r.rule.project_id}"
                )
        elif shadow_rules and cfg.promotion_enabled:
            print()
            print("shadow_pool HOT  (0 — no shadow HOT on this prompt)")
    finally:
        db.close()
    return 0
