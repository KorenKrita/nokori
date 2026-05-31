from __future__ import annotations

import argparse

from ..config import Config
from ..db import open_db
from ..lifecycle import maintenance


def run(_args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        summary = maintenance.run_due_jobs(db, cfg)
    finally:
        db.close()
    print(f"dormant_scan       moved={summary['dormant_scan']}")
    print(f"candidate_cleanup  deleted={summary['candidate_cleanup']}")
    print(f"injection_cleanup  deleted={summary['injection_cleanup']}")
    print(f"unmerge_check      restored={summary['unmerge_check']}")
    return 0
