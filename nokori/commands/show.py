from __future__ import annotations

import argparse

from ..config import Config
from ..db import fetch_rule_by_short_id, open_db
from ..errors import NokoriError


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, args.short_id)
    finally:
        db.close()

    if rule is None:
        raise NokoriError(f"no rule with short_id {args.short_id!r}")

    print(f"id              {rule.id}")
    print(f"short_id        {rule.short_id}")
    print(f"status          {rule.status}")
    print(f"source_type     {rule.source_type}")
    print(f"confidence      {rule.confidence}")
    print(f"evidence_score  {rule.evidence_score}")
    print(f"hit_count       {rule.hit_count}")
    print(f"last_hit        {rule.last_hit}")
    print(f"project_scope   {rule.project_scope}")
    print(f"project_id      {rule.project_id}")
    print(f"superseded_by   {rule.superseded_by or '-'}")
    print(f"archived_reason {rule.archived_reason or '-'}")
    print(f"created_at      {rule.created_at}")
    print(f"updated_at      {rule.updated_at}")
    print()
    print(f"trigger:    {rule.trigger_text}")
    for v in rule.trigger_variants:
        print(f"  variant : {v}")
    print(f"behavior:   {rule.behavior or '-'}")
    print(f"action:     {rule.action}")
    print(f"rationale:  {rule.rationale or '-'}")
    if rule.search_terms:
        print("search_terms:")
        for lang, items in rule.search_terms.items():
            print(f"  {lang}: {', '.join(items)}")
    return 0
