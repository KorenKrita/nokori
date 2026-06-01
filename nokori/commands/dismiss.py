from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..db import (
    archive_rule,
    fetch_rule_by_short_id,
    find_rule_id_injected_since,
    open_db,
)
from ..errors import NokoriError
from ..gate import marker as marker_io
from ..utils.time import iso_of, now_iso


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, args.short_id)
        if rule is None:
            raise NokoriError(f"no rule with short_id {args.short_id!r}")
        if rule.status == "archived":
            print(f"{rule.short_id} already archived")
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        cutoff_iso = iso_of(cutoff)
        if find_rule_id_injected_since(db, rule.short_id, cutoff_iso) is None:
            raise NokoriError(
                f"rule {args.short_id!r} was not injected in the last 24 hours "
                "(any session); wait until after injection or check short_id"
            )
        now = now_iso()
        archive_rule(db, rule.id, "user_dismissed_cli", now)
        cleared = marker_io.strip_short_id_from_all_markers(cfg, rule.short_id)
    finally:
        db.close()
    print(f"dismissed {args.short_id}")
    if cleared:
        print(f"cleared gate markers referencing {args.short_id} ({cleared} file(s))")
    return 0
