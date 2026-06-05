from __future__ import annotations

import argparse

from ..cold.jobs import expire_stale_ingest_jobs
from ..config import Config
from ..db import fetch_rules, open_db
from ..lifecycle import maintenance
from ..llm.adapter import LLMAdapter
from ..events.shadow import run_shadow_counterfactual_evaluation
from ..posthoc.jobs import process_pending_posthoc_jobs
from ..search.idf_stats import (
    build_idf_stats,
    compute_eligible_rule_set_hash,
    store_idf_stats,
)


# Cached pool hash to detect pool changes between runs
_last_idf_pool_hash: str | None = None


class _PosthocLLMAdapter:
    def __init__(self, cfg: Config):
        self._llm = LLMAdapter(cfg)

    def call(self, *, system: str, user: str, role: str):
        return self._llm.complete_role(role, system, user)


def run(_args: argparse.Namespace, cfg: Config) -> int:
    if cfg.disabled:
        print("nokori: disabled (NOKORI_DISABLED)")
        return 0

    db = open_db(cfg.db_path)
    try:
        # Core maintenance (includes lifecycle transitions via run_maintenance)
        summary = maintenance.run_maintenance(db, cfg)

        # Cold-path posthoc/shadow evaluation jobs
        llm = _PosthocLLMAdapter(cfg)
        posthoc_summary = process_pending_posthoc_jobs(
            db, llm, limit=20
        )
        shadow_summary = run_shadow_counterfactual_evaluation(
            db, llm, limit=20
        )

        # Expire stale transcript ingest jobs
        expired_ingest = expire_stale_ingest_jobs(db)

        # Rebuild IDF stats if eligible pool changed
        global _last_idf_pool_hash
        eligible_rules = fetch_rules(db, statuses=("active", "trusted"))
        current_hash = compute_eligible_rule_set_hash(eligible_rules)
        idf_rebuilt = False
        if current_hash != _last_idf_pool_hash:
            store_idf_stats(db, build_idf_stats(eligible_rules))
            _last_idf_pool_hash = current_hash
            idf_rebuilt = True
    finally:
        db.close()

    print(f"transitions.applied   {summary['transitions_applied']}")
    print(f"candidate_cleanup     deleted={summary['candidate_cleanup']}")
    print(f"injection_cleanup     deleted={summary['injection_cleanup']}")
    print(f"unmerge_check         restored={summary['unmerge_check']}")
    print(
        "posthoc.processed     "
        f"done={posthoc_summary['done']} "
        f"unclear={posthoc_summary['unclear']} "
        f"failed={posthoc_summary['failed']}"
    )
    print(
        "shadow.processed    "
        f"processed={shadow_summary['processed']} "
        f"labeled={shadow_summary['labeled']} "
        f"failed={shadow_summary['failed']} "
        f"transitions={shadow_summary['transitions_applied']}"
    )
    print(f"ingest.expired        {expired_ingest}")
    print(f"idf.rebuilt           {idf_rebuilt}")
    return 0
