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
from ..utils.time import now_iso


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
        cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
        if find_rule_id_injected_since(db, rule.short_id, cutoff_iso) is None:
            raise NokoriError(
                f"rule {args.short_id!r} was not injected in the last 24 hours; "
                "dismiss from the session where it was shown, or wait until after injection"
            )
        now = now_iso()
        archive_rule(db, rule.id, "user_dismissed_cli", now)
    finally:
        db.close()
    print(f"dismissed {args.short_id}")
    return 0
