from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..db import open_db


def run(_args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        version = db.schema_version()
        rules = db.fetchall("SELECT status, COUNT(*) AS n FROM rules GROUP BY status")
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        injected_24h = db.fetchone(
            "SELECT COUNT(*) AS n FROM injections WHERE created_at >= ?",
            (cutoff,),
        )
    finally:
        db.close()

    by_status = {r["status"]: r["n"] for r in rules}
    total = sum(by_status.values())

    print(f"data_dir       {cfg.data_dir}")
    print(f"db             {cfg.db_path}")
    print(f"schema_version {version}")
    print(f"rules.total    {total}")
    print(f"rules.active   {by_status.get('active', 0)}")
    print(f"rules.dormant  {by_status.get('dormant', 0)}")
    print(f"rules.candidate {by_status.get('candidate', 0)}")
    print(f"rules.merged   {by_status.get('merged', 0)}")
    print(f"rules.archived {by_status.get('archived', 0)}")
    print(f"injections.last_24h {injected_24h['n'] if injected_24h else 0}")
    print(f"gate.enabled   {cfg.gate_enabled}")
    print(f"extract.mode   {cfg.extract_mode}")
    print(f"llm.configured {bool(cfg.llm_base_url and cfg.llm_model)}")
    print(f"embed.configured {bool(cfg.embed_base_url and cfg.embed_model)}")
    return 0
