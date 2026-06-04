from __future__ import annotations

import argparse

from ..archive.fingerprints import create_archived_fingerprint
from ..config import Config
from ..db import (
    archive_rule,
    fetch_rule_by_short_id,
    open_db,
)
from ..errors import NokoriError
from ..gate.marker import strip_short_id_from_all_markers
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

        now = now_iso()
        archive_rule(db, rule.id, "user_archived", now)
        create_archived_fingerprint(db, rule, strength="user")
        strip_short_id_from_all_markers(cfg, rule.short_id)
    finally:
        db.close()
    print(f"archived {args.short_id}")
    return 0
