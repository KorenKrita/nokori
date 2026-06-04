from __future__ import annotations

import argparse

from ..cold.jobs import expire_stale_ingest_jobs
from ..config import Config
from ..db import fetch_rules, open_db
from ..lifecycle import maintenance
from ..posthoc.jobs import get_pending_posthoc_jobs
from ..search.idf_stats import build_idf_stats, compute_eligible_rule_set_hash


# Cached pool hash to detect pool changes between runs
_last_idf_pool_hash: str | None = None


def run(_args: argparse.Namespace, cfg: Config) -> int:
    if cfg.disabled:
        print("nokori: disabled (NOKORI_DISABLED)")
        return 0

    db = open_db(cfg.db_path)
    try:
        # Core maintenance (includes lifecycle transitions via run_maintenance)
        summary = maintenance.run_maintenance(db, cfg)

        # Pending posthoc jobs count (actual processing is cold-path worker)
        pending_posthoc = get_pending_posthoc_jobs(db, limit=100)
        posthoc_pending_count = len(pending_posthoc)

        # Expire stale transcript ingest jobs
        expired_ingest = expire_stale_ingest_jobs(db)

        # Rebuild IDF stats if eligible pool changed
        global _last_idf_pool_hash
        eligible_rules = fetch_rules(db, statuses=("active", "trusted"))
        current_hash = compute_eligible_rule_set_hash(eligible_rules)
        idf_rebuilt = False
        if current_hash != _last_idf_pool_hash:
            build_idf_stats(eligible_rules)
            _last_idf_pool_hash = current_hash
            idf_rebuilt = True
    finally:
        db.close()

    print(f"transitions.applied   {summary['transitions_applied']}")
    print(f"candidate_cleanup     deleted={summary['candidate_cleanup']}")
    print(f"injection_cleanup     deleted={summary['injection_cleanup']}")
    print(f"unmerge_check         restored={summary['unmerge_check']}")
    print(f"posthoc.pending       {posthoc_pending_count}")
    print(f"ingest.expired        {expired_ingest}")
    print(f"idf.rebuilt           {idf_rebuilt}")
    return 0
