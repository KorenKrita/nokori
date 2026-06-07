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
    loads_json,
    fetch_rules,
    fetch_short_ids,
    open_db,
)
from ..errors import NokoriError
from ..matcher.compiler import CompilationError, compile_rule
from ..policy import RUNTIME_POLICY_VERSION
from ..utils.ids import new_uuid, short_id_for
from ..utils.time import now_iso

_COMPATIBLE_IMPORT_VERSIONS = frozenset({6, 7})

_MAX_TRIGGER_TEXT = 16_384
_MAX_ACTION = 8_192
_MAX_SHORT_ID = 64
_MAX_VARIANTS = 32
_MAX_VARIANT_LEN = 512
_MAX_SEARCH_LANGS = 16
_MAX_IMPORT_RULES = 10_000
_MAX_IMPORT_FILE_BYTES = 100 * 1024 * 1024
_STATUSES = frozenset({"candidate", "active", "trusted", "suppressed", "archived"})
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


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = loads_json(value, [])
        return parsed if isinstance(parsed, list) else []
    return []


def _json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = loads_json(value, {})
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _validate_import_record(rec: dict) -> str | None:
    trigger = rec.get("trigger_canonical")
    if trigger is not None and not str(trigger).strip():
        return "trigger_canonical must not be empty"
    action = rec.get("action_instruction")
    if action is not None and not str(action).strip():
        return "action_instruction must not be empty"
    for field, limit in (
        ("trigger_canonical", _MAX_TRIGGER_TEXT),
        ("action_instruction", _MAX_ACTION),
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
        if isinstance(v, dict):
            err = _str_len(v.get("text"), f"trigger_variants[{i}].text", _MAX_VARIANT_LEN)
            if err:
                return err
        else:
            err = _str_len(v, f"trigger_variants[{i}]", _MAX_VARIANT_LEN)
            if err:
                return err
    terms = rec.get("search_terms") or {}
    if not isinstance(terms, dict):
        return "search_terms must be an object"
    if len(terms) > _MAX_SEARCH_LANGS:
        return f"search_terms exceeds {_MAX_SEARCH_LANGS} languages"
    status = rec.get("status", "candidate")
    if status not in _STATUSES:
        return f"status must be one of {sorted(_STATUSES)}"
    scope = rec.get("project_scope", "global")
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
    matcher_err = _validate_matcher_structure(rec)
    if matcher_err:
        return matcher_err
    return None


def _normalize_variants_for_compile(rec: dict) -> list[dict]:
    groups = rec.get("required_concept_groups") or []
    required_concepts = []
    if groups and isinstance(groups[0], dict):
        required_concepts = list(groups[0].get("all_of") or [])
    variants = []
    for variant in rec.get("trigger_variants") or []:
        if isinstance(variant, dict):
            variants.append(variant)
            continue
        text = str(variant).strip()
        if not text:
            continue
        variants.append({
            "text": text,
            "kind": "weak_recall",
            "requires_concepts": [],
        })
    trigger = str(rec.get("trigger_canonical") or "").strip()
    if trigger and required_concepts and len(re.findall(r"[\w+-]+", trigger)) >= 2:
        variants.append({
            "text": trigger,
            "kind": "strong_anchor",
            "requires_concepts": required_concepts,
        })
    return variants


def _validate_matcher_structure(rec: dict) -> str | None:
    status = rec.get("status", "candidate")
    if status == "archived":
        return None
    concepts = rec.get("concepts") or []
    groups = rec.get("required_concept_groups") or []
    excluded_contexts = rec.get("excluded_contexts") or []
    variants = rec.get("trigger_variants") or []
    if not isinstance(concepts, list) or not concepts or not isinstance(groups, list) or not groups:
        return (
            "concepts and required_concept_groups must be non-empty lists "
            "for non-archived imports"
        )
    if not isinstance(excluded_contexts, list):
        return "excluded_contexts must be a list"
    if not isinstance(variants, list) or not variants:
        return "trigger_variants must be a non-empty list for non-archived imports"
    try:
        compile_rule(
            {
                "concepts": concepts,
                "required_concept_groups": groups,
                "excluded_contexts": excluded_contexts,
                "variants": _normalize_variants_for_compile(rec),
                "trigger_canonical": rec.get("trigger_canonical", ""),
            },
            search_terms=rec.get("search_terms") or {},
        )
    except (CompilationError, TypeError, AttributeError) as e:
        return f"matcher compilation failed: {e}"
    return None


def _import_status(rec: dict) -> str:
    """Import preserves archive vetoes; all other records re-enter as candidates."""
    return "archived" if rec.get("status", "candidate") == "archived" else "candidate"


def _import_source_origin(rec: dict) -> str:
    """Imported non-archive records are external source material, not trusted state."""
    return (
        rec.get("source_origin", "transcript_extraction")
        if rec.get("status", "candidate") == "archived"
        else "external_source_material"
    )


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
                "schema_version": r.schema_version,
                "rule_version": r.rule_version,
                "created_by_pipeline_version": r.created_by_pipeline_version,
                "runtime_policy_version": r.runtime_policy_version,
                "status": r.status,
                "severity": r.severity,
                "trigger_canonical": r.trigger_canonical,
                "trigger_canonical_zh": r.trigger_canonical_zh,
                "concepts": _json_list(r.concepts),
                "required_concept_groups": _json_list(r.required_concept_groups),
                "excluded_contexts": _json_list(r.excluded_contexts),
                "near_miss_examples": _json_list(r.near_miss_examples),
                "trigger_variants": _json_list(r.trigger_variants),
                "trigger_variants_zh": _json_list(r.trigger_variants_zh),
                "search_terms": _json_dict(r.search_terms),
                "action_instruction": r.action_instruction,
                "action_instruction_zh": r.action_instruction_zh,
                "domain_tags": _json_list(r.domain_tags),
                "tool_tags": _json_list(r.tool_tags),
                "path_patterns": _json_list(r.path_patterns),
                "source_origin": r.source_origin,
                "project_scope": r.project_scope,
                "project_id": r.project_id,
                "archived_reason": r.archived_reason,
                "replacement_id": r.replacement_id,
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
    print(f"exported {len(rules)} rules -> {target}")
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
    if file_schema not in _COMPATIBLE_IMPORT_VERSIONS:
        raise NokoriError(
            f"export schema version {file_schema} incompatible with this nokori "
            f"(rules.db expects {SCHEMA_VERSION}); re-export or use matching release"
        )
    rules_in = data.get("rules") or []
    if len(rules_in) > _MAX_IMPORT_RULES:
        raise NokoriError(
            f"import file contains {len(rules_in)} rules, exceeds limit of {_MAX_IMPORT_RULES}"
        )

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
                SCHEMA_VERSION,
                rec.get("rule_version", 1),
                rec.get("created_by_pipeline_version", "import_v6"),
                RUNTIME_POLICY_VERSION,
                rec.get("trigger_canonical", ""),
                rec.get("trigger_canonical_zh"),
                dumps_json(rec.get("concepts") or []),
                dumps_json(rec.get("required_concept_groups") or []),
                dumps_json(rec.get("excluded_contexts") or []),
                dumps_json(rec.get("near_miss_examples") or []),
                dumps_json(rec.get("trigger_variants") or []),
                dumps_json(rec.get("trigger_variants_zh") or []),
                dumps_json(rec.get("search_terms") or {}),
                rec.get("action_instruction", ""),
                rec.get("action_instruction_zh"),
                dumps_json(rec.get("domain_tags") or []),
                dumps_json(rec.get("tool_tags") or []),
                dumps_json(rec.get("path_patterns") or []),
                _import_status(rec),
                rec.get("severity", "reminder"),
                _import_source_origin(rec),
                rec.get("project_scope", "global"),
                rec.get("project_id"),
                rec.get("archived_reason"),
                rec.get("replacement_id"),
                rec.get("created_at") or now_iso(),
                rec.get("updated_at") or now_iso(),
            ))
            inserted_sids.append(sid)
        if pending:
            with db.transaction() as tx:
                for row in pending:
                    tx.execute(
                        "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                        "created_by_pipeline_version, runtime_policy_version, "
                        "trigger_canonical, trigger_canonical_zh, "
                        "concepts, required_concept_groups, excluded_contexts, "
                        "near_miss_examples, "
                        "trigger_variants, trigger_variants_zh, search_terms, "
                        "action_instruction, action_instruction_zh, "
                        "domain_tags, tool_tags, path_patterns, "
                        "status, severity, source_origin, "
                        "project_scope, project_id, "
                        "archived_reason, replacement_id, "
                        "created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        row,
                    )
            inserted = len(pending)
    finally:
        db.close()

    print(f"imported {inserted} rules (skipped {skipped} duplicates)")
    if inserted_sids:
        print(f"  new: {', '.join(inserted_sids[:10])}")
    return 0
