from __future__ import annotations

import argparse

from ..config import Config
from ..db import dumps_json, fetch_rule_by_short_id, fetch_short_ids, open_db
from ..errors import NokoriError
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.text import split_csv
from ..utils.time import now_iso


_MAX_TRIGGER = 16_384
_MAX_ACTION = 8_192


def run(args: argparse.Namespace, cfg: Config) -> int:
    if len(args.trigger.strip()) < 3:
        raise NokoriError("trigger must be at least 3 non-whitespace characters")
    if len(args.trigger) > _MAX_TRIGGER:
        raise NokoriError(f"trigger exceeds {_MAX_TRIGGER} characters")
    if not args.action or not args.action.strip():
        raise NokoriError("action must not be empty")
    if len(args.action) > _MAX_ACTION:
        raise NokoriError(f"action exceeds {_MAX_ACTION} characters")
    now = now_iso()
    rid = new_uuid()

    variants = split_csv(args.variants)
    terms: dict[str, list[str]] = {}
    if args.terms_en:
        terms["en"] = split_csv(args.terms_en)
    if args.terms_zh:
        terms["zh"] = split_csv(args.terms_zh)

    is_correction = (args.confidence == "high" and args.source_type == "correction")
    status = "active" if is_correction else "candidate"
    project_id = args.project_id
    project_scope = "project" if project_id else "global"
    evidence_score = 3.0 if is_correction else 0.0
    activation_origin = "cold_fast_lane" if is_correction else None
    severity = getattr(args, "severity", "reminder") or "reminder"

    db = open_db(cfg.db_path)
    try:
        existing = fetch_short_ids(db)
        sid = short_id_for(rid, existing)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, trigger_variants, "
                "search_terms, action_instruction, "
                "source_origin, status, severity, "
                "evidence_support_score, activation_origin, "
                "project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    sid,
                    1,
                    1,
                    "cli_add_v1",
                    "policy_v1",
                    args.trigger,
                    dumps_json(variants),
                    dumps_json(terms),
                    args.action,
                    "transcript_extraction",
                    status,
                    severity,
                    evidence_score,
                    activation_origin,
                    project_scope,
                    project_id,
                    now,
                    now,
                ),
            )
        rule = fetch_rule_by_short_id(db, sid)
        if rule:
            index_rule_if_enabled(db, rule, cfg)
    finally:
        db.close()

    print(f"added {sid} ({status})")
    return 0
