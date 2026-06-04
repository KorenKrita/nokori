from __future__ import annotations

import argparse

from ..config import Config
from ..db import SCHEMA_VERSION, dumps_json, fetch_rule_by_short_id, fetch_short_ids, open_db
from ..errors import NokoriError
from ..policy import RUNTIME_POLICY_VERSION
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.text import split_csv
from ..utils.time import now_iso


_MAX_TRIGGER = 16_384
_MAX_ACTION = 8_192


def _manual_trigger_structure(trigger: str, variants: list[str]) -> tuple[list[dict], list[dict]]:
    concept_id = "manual_trigger"
    aliases = [{"text": trigger, "strength": "strong"}]
    aliases.extend({"text": v, "strength": "strong"} for v in variants)
    concepts = [{
        "id": concept_id,
        "label": trigger[:80],
        "aliases": aliases,
        "match_mode": "phrase",
        "required": True,
    }]
    groups = [{"id": "manual_primary", "all_of": [concept_id]}]
    return concepts, groups


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

    status = "candidate"
    project_id = args.project_id
    project_scope = "project" if project_id else "global"
    evidence_score = 0.0
    activation_origin = None
    severity = getattr(args, "severity", "reminder") or "reminder"
    concepts, groups = _manual_trigger_structure(args.trigger, variants)

    db = open_db(cfg.db_path)
    try:
        existing = fetch_short_ids(db)
        sid = short_id_for(rid, existing)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, concepts, required_concept_groups, trigger_variants, "
                "search_terms, action_instruction, "
                "source_origin, status, severity, "
                "evidence_support_score, activation_origin, "
                "project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    sid,
                    SCHEMA_VERSION,
                    1,
                    "cli_add_v6",
                    RUNTIME_POLICY_VERSION,
                    args.trigger,
                    dumps_json(concepts),
                    dumps_json(groups),
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
