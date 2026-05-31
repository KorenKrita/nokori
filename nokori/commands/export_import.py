from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from pathlib import Path

from ..config import Config
from ..db import (
    SCHEMA_VERSION,
    dumps_json,
    fetch_rule_by_short_id,
    fetch_rules,
    fetch_short_ids,
    open_db,
)
from ..errors import NokoriError
from ..search.embedding import index_rule_if_enabled
from ..utils.ids import new_uuid, short_id_for
from ..utils.time import now_iso

_MAX_TRIGGER_TEXT = 16_384
_MAX_ACTION = 8_192
_MAX_RATIONALE = 4_096
_MAX_BEHAVIOR = 4_096
_MAX_SHORT_ID = 64
_MAX_VARIANTS = 32
_MAX_VARIANT_LEN = 512
_MAX_SEARCH_LANGS = 16
_MAX_TERMS_PER_LANG = 64
_MAX_TERM_LEN = 256
_MAX_IMPORT_FILE_BYTES = 100 * 1024 * 1024
_SOURCE_TYPES = frozenset({"correction", "preference", "solution", "anti_pattern"})
_CONFIDENCE = frozenset({"high", "medium"})
_STATUSES = frozenset({"candidate", "active", "merged", "archived", "dormant"})
_PROJECT_SCOPES = frozenset({"project", "global"})
_HEX_SHORT_ID = re.compile(r"^[a-f0-9]{6,32}$", re.IGNORECASE)


def _str_len(value: object, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return f"{field} must be a string"
    if len(value) > limit:
        return f"{field} exceeds {limit} characters"
    return None


def _validate_import_record(rec: dict) -> str | None:
    trigger = rec.get("trigger_text")
    if trigger is not None and not str(trigger).strip():
        return "trigger_text must not be empty"
    action = rec.get("action")
    if action is not None and not str(action).strip():
        return "action must not be empty"
    for field, limit in (
        ("trigger_text", _MAX_TRIGGER_TEXT),
        ("action", _MAX_ACTION),
        ("rationale", _MAX_RATIONALE),
        ("behavior", _MAX_BEHAVIOR),
        ("short_id", _MAX_SHORT_ID),
    ):
        err = _str_len(rec.get(field), field, limit)
        if err:
            return err
    variants = rec.get("trigger_variants") or []
    if not isinstance(variants, list):
        return "trigger_variants must be a list"
    if len(variants) > _MAX_VARIANTS:
        return f"trigger_variants exceeds {_MAX_VARIANTS} entries"
    for i, v in enumerate(variants):
        err = _str_len(v, f"trigger_variants[{i}]", _MAX_VARIANT_LEN)
        if err:
            return err
    terms = rec.get("search_terms") or {}
    if not isinstance(terms, dict):
        return "search_terms must be an object"
    if len(terms) > _MAX_SEARCH_LANGS:
        return f"search_terms exceeds {_MAX_SEARCH_LANGS} languages"
    for lang, lang_terms in terms.items():
        if not isinstance(lang, str) or len(lang) > 32:
            return "search_terms language key invalid"
        if not isinstance(lang_terms, list):
            return f"search_terms[{lang!r}] must be a list"
        if len(lang_terms) > _MAX_TERMS_PER_LANG:
            return f"search_terms[{lang!r}] exceeds {_MAX_TERMS_PER_LANG} terms"
        for j, term in enumerate(lang_terms):
            err = _str_len(term, f"search_terms[{lang!r}][{j}]", _MAX_TERM_LEN)
            if err:
                return err
    st = rec.get("source_type", "correction")
    if st not in _SOURCE_TYPES:
        return f"source_type must be one of {sorted(_SOURCE_TYPES)}"
    conf = rec.get("confidence", "medium")
    if conf not in _CONFIDENCE:
        return f"confidence must be one of {sorted(_CONFIDENCE)}"
    status = rec.get("status", "candidate")
    if status not in _STATUSES:
        return f"status must be one of {sorted(_STATUSES)}"
    scope = rec.get("project_scope", "project")
    if scope not in _PROJECT_SCOPES:
        return f"project_scope must be one of {sorted(_PROJECT_SCOPES)}"
    rid = rec.get("id")
    if rid is not None:
        try:
            uuid.UUID(str(rid))
        except ValueError:
            return "id must be a valid UUID"
    sid = rec.get("short_id")
    if sid is not None and not _HEX_SHORT_ID.match(str(sid)):
        return "short_id must be 6-32 hexadecimal characters"
    for field in ("evidence_score", "hit_count", "shadow_hit_count"):
        val = rec.get(field, 0)
        if val is None:
            continue
        if not isinstance(val, int) or isinstance(val, bool):
            return f"{field} must be an integer"
    return None


def run_export(args: argparse.Namespace, cfg: Config) -> int:
    target = Path(args.path).expanduser().resolve()
    db = open_db(cfg.db_path)
    try:
        rules = fetch_rules(db, statuses=None)
    finally:
        db.close()

    payload = {
        "format": "nokori-export",
        "version": SCHEMA_VERSION,
        "exported_at": now_iso(),
        "rules": [
            {
                "id": r.id,
                "short_id": r.short_id,
                "trigger_text": r.trigger_text,
                "trigger_variants": r.trigger_variants,
                "search_terms": r.search_terms,
                "behavior": r.behavior,
                "action": r.action,
                "rationale": r.rationale,
                "source_type": r.source_type,
                "confidence": r.confidence,
                "status": r.status,
                "evidence_score": r.evidence_score,
                "evidence_log": r.evidence_log,
                "hit_count": r.hit_count,
                "last_hit": r.last_hit,
                "shadow_hit_count": r.shadow_hit_count,
                "promotion_evidence": r.promotion_evidence,
                "project_scope": r.project_scope,
                "project_id": r.project_id,
                "superseded_by": r.superseded_by,
                "archived_reason": r.archived_reason,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rules
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    print(f"exported {len(rules)} rules → {target}")
    return 0


def run_import(args: argparse.Namespace, cfg: Config) -> int:
    src = Path(args.path).expanduser().resolve()
    if not src.exists():
        raise NokoriError(f"file not found: {src}")
    try:
        size = src.stat().st_size
    except OSError as e:
        raise NokoriError(f"cannot read import file: {e}") from e
    if size > _MAX_IMPORT_FILE_BYTES:
        raise NokoriError(
            f"import file exceeds {_MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MiB limit"
        )
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NokoriError(f"invalid JSON: {e}") from e
    if data.get("format") != "nokori-export":
        raise NokoriError("unrecognized export format")
    file_schema = data.get("version")
    if file_schema is None:
        raise NokoriError(
            "export missing version field (must match rules.db PRAGMA user_version)"
        )
    try:
        file_schema = int(file_schema)
    except (TypeError, ValueError) as e:
        raise NokoriError(f"invalid export version: {file_schema!r}") from e
    if file_schema != SCHEMA_VERSION:
        raise NokoriError(
            f"export schema version {file_schema} incompatible with this nokori "
            f"(rules.db expects {SCHEMA_VERSION}); re-export or use matching release"
        )
    rules_in = data.get("rules") or []

    db = open_db(cfg.db_path)
    inserted = skipped = 0
    inserted_sids: list[str] = []
    try:
        existing_ids = {r["id"] for r in db.fetchall("SELECT id FROM rules")}
        existing_short_ids = fetch_short_ids(db)
        pending: list[tuple] = []
        for rec in rules_in:
            if not isinstance(rec, dict):
                raise NokoriError("each rule must be an object")
            err = _validate_import_record(rec)
            if err:
                raise NokoriError(f"invalid import rule: {err}")
            if rec.get("id") in existing_ids:
                skipped += 1
                continue
            rid = rec.get("id") or new_uuid()
            sid = rec.get("short_id") or short_id_for(rid, existing_short_ids)
            if sid in existing_short_ids:
                sid = short_id_for(rid, existing_short_ids)
            existing_short_ids.add(sid)
            existing_ids.add(rid)
            pending.append((
                rid, sid,
                rec.get("trigger_text", ""),
                dumps_json(rec.get("trigger_variants") or []),
                dumps_json(rec.get("search_terms") or {}),
                rec.get("behavior"),
                rec.get("action", ""),
                rec.get("rationale"),
                rec.get("source_type", "correction"),
                rec.get("confidence", "medium"),
                rec.get("status", "candidate"),
                rec.get("evidence_score", 0),
                dumps_json(rec.get("evidence_log") or []),
                rec.get("hit_count", 0),
                rec.get("last_hit"),
                rec.get("shadow_hit_count", 0),
                dumps_json(rec.get("promotion_evidence") or []),
                rec.get("project_scope", "project"),
                rec.get("project_id"),
                rec.get("superseded_by"),
                rec.get("archived_reason"),
                rec.get("created_at") or now_iso(),
                rec.get("updated_at") or now_iso(),
            ))
            inserted_sids.append(sid)
        if pending:
            with db.transaction() as tx:
                for row in pending:
                    tx.execute(
                        "INSERT INTO rules (id, short_id, trigger_text, trigger_variants, "
                        "search_terms, behavior, action, rationale, source_type, confidence, "
                        "status, evidence_score, evidence_log, hit_count, last_hit, "
                        "shadow_hit_count, promotion_evidence, project_scope, project_id, "
                        "superseded_by, archived_reason, "
                        "created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        row,
                    )
            inserted = len(pending)
        for sid in inserted_sids:
            rule = fetch_rule_by_short_id(db, sid)
            if rule and rule.status in ("active", "dormant"):
                index_rule_if_enabled(db, rule, cfg)
    finally:
        db.close()
    print(f"imported {inserted} rules; skipped {skipped} (already present)")
    return 0
