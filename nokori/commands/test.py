from __future__ import annotations

import argparse

from ..config import Config
from ..db import fetch_rules, fetch_shadow_rules, open_db
from ..search import bm25, ranker
from ..search import embedding as embedding_search


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(
            db, statuses=("active", "dormant"), project_id=args.project
        )

        bm25_results = bm25.search(args.prompt, rules, top_k=10)

        embed_results = []
        embed_mode = "off"
        if embedding_search.auto_enabled(cfg, len(rules)):
            if embedding_search.use_local(cfg):
                client = embedding_search.LocalEmbeddingClient(cfg)
                if client.available():
                    embed_results = embedding_search.search_local(
                        args.prompt, rules, db, client, top_k=10
                    )
                    embed_mode = "local"
            else:
                client = embedding_search.EmbeddingClient(cfg)
                embed_results = embedding_search.search(
                    args.prompt, rules, db, client, top_k=10
                )
                embed_mode = "remote"

        fused = ranker.rrf_fuse(bm25_results, embed_results)
        hot, warm = ranker.tier_results(fused)

        print(f"prompt        {args.prompt!r}")
        print(f"candidates    {len(rules)} rules in pool")
        print(f"bm25.matches  {len(bm25_results)}")
        print(f"embed.mode    {embed_mode} ({len(embed_results)} results)")
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

        # Shadow pool preview (same threshold as hook)
        if args.project:
            shadow_rules = fetch_shadow_rules(db, project_id=args.project)
            if shadow_rules:
                shadow_bm25 = bm25.search(args.prompt, shadow_rules, top_k=5)
                shadow_fused = ranker.rrf_fuse(shadow_bm25, [])
                shadow_hits = [
                    r for r in shadow_fused
                    if r.rrf_score >= ranker.MIN_ABSOLUTE_SCORE
                    and ranker._meets_min_evidence(r)
                ]
                if shadow_hits:
                    print()
                    print(f"shadow_pool ({len(shadow_hits)} hits, not injected):")
                    for r in shadow_hits[:3]:
                        print(
                            f"  {r.rule.short_id}  bm25={r.bm25_score:.4f}  "
                            f"proj={r.rule.project_id}"
                        )
    finally:
        db.close()
    return 0
