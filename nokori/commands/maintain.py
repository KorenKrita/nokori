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
        summary: dict | None = None
        try:
            summary = maintenance.run_maintenance(db, cfg)
        except Exception as exc:
            print(f"maintain: run_maintenance failed: {exc}")

        # Cold-path posthoc/shadow evaluation jobs
        llm = _PosthocLLMAdapter(cfg)
        posthoc_summary: dict | None = None
        try:
            posthoc_summary = process_pending_posthoc_jobs(
                db, llm, limit=20
            )
        except Exception as exc:
            print(f"maintain: process_pending_posthoc_jobs failed: {exc}")

        shadow_summary: dict | None = None
        try:
            shadow_summary = run_shadow_counterfactual_evaluation(
                db, llm, limit=20
            )
        except Exception as exc:
            print(f"maintain: run_shadow_counterfactual_evaluation failed: {exc}")

        # Expire stale transcript ingest jobs
        expired_ingest: int | None = None
        try:
            expired_ingest = expire_stale_ingest_jobs(db)
        except Exception as exc:
            print(f"maintain: expire_stale_ingest_jobs failed: {exc}")

        # Rebuild IDF stats if eligible pool changed
        idf_rebuilt = False
        try:
            eligible_rules = fetch_rules(db, statuses=("active", "trusted"))
            current_hash = compute_eligible_rule_set_hash(eligible_rules)
            last_row = db.fetchone(
                "SELECT eligible_rule_set_hash FROM trigger_idf_stats "
                "ORDER BY built_at DESC LIMIT 1"
            )
            last_hash = last_row["eligible_rule_set_hash"] if last_row else None
            if current_hash != last_hash:
                store_idf_stats(db, build_idf_stats(eligible_rules))
                idf_rebuilt = True
        except Exception as exc:
            print(f"maintain: IDF rebuild failed: {exc}")
    finally:
        db.close()

    if summary:
        print(f"transitions.applied   {summary['transitions_applied']}")
        print(f"candidate_cleanup     deleted={summary['candidate_cleanup']}")
        print(f"injection_cleanup     deleted={summary['injection_cleanup']}")
        print(f"unmerge_check         restored={summary['unmerge_check']}")
    else:
        print("transitions.applied   (failed)")

    if posthoc_summary:
        print(
            "posthoc.processed     "
            f"done={posthoc_summary['done']} "
            f"unclear={posthoc_summary['unclear']} "
            f"failed={posthoc_summary['failed']}"
        )
    else:
        print("posthoc.processed     (failed)")

    if shadow_summary:
        print(
            "shadow.processed    "
            f"processed={shadow_summary['processed']} "
            f"labeled={shadow_summary['labeled']} "
            f"failed={shadow_summary['failed']} "
            f"transitions={shadow_summary['transitions_applied']}"
        )
    else:
        print("shadow.processed      (failed)")

    print(f"ingest.expired        {expired_ingest if expired_ingest is not None else '(failed)'}")
    print(f"idf.rebuilt           {idf_rebuilt}")
    return 0
