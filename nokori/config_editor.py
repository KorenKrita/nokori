"""Config editor: merge user file values, defaults, and save semantics."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from .config import Config, _resolve_file_values
from .config_file import (
    apply_patch,
    config_path,
    env_keys_for_path,
    get_nested,
    list_set_paths,
    load_document,
)
from .config_schema import (
    FIELD_BY_ID,
    FIELDS,
    SECTION_LABELS,
    VARIANT_LABELS,
    FieldDef,
    Locale,
    SECTIONS,
)
from .errors import ConfigError
from .search.embedding import remote_embed_configured

EmbedMode = Literal["local", "remote"]

_REMOTE_CLEAR = ("embed.base_url", "embed.model", "embed.api_key")
_LOCAL_CLEAR = (
    "embed.hook_timeout_seconds",
    "embed.server_idle_seconds",
    "embed.server_auto_start",
)


def _normalize_locale(raw: str | None) -> Locale:
    if not raw:
        return "en"
    low = raw.strip().lower()
    if low.startswith("zh"):
        return "zh"
    if low.startswith("ja"):
        return "ja"
    return "en"


def _field_to_api(field: FieldDef, locale: Locale) -> dict[str, Any]:
    return {
        "id": field.id,
        "type": field.field_type,
        "default": field.default,
        "options": list(field.options) if field.options else None,
        "min_value": field.min_value,
        "read_only": field.read_only,
        "exclusive_group": field.exclusive_group,
        "exclusive_variant": field.exclusive_variant,
        "label": field.label.get(locale) or field.label.get("en") or field.id,
        "description": field.description.get(locale) or field.description.get("en") or "",
    }


def _schema_payload(locale: Locale) -> dict[str, Any]:
    sections_out = []
    for sec in SECTIONS:
        item: dict[str, Any] = {
            "id": sec["id"],
            "label": SECTION_LABELS.get(sec["id"], {}).get(locale)
            or SECTION_LABELS.get(sec["id"], {}).get("en")
            or sec["id"],
            "fields": [_field_to_api(FIELD_BY_ID[fid], locale) for fid in sec["field_ids"]],
        }
        if "exclusive" in sec:
            ex = sec["exclusive"]
            item["exclusive"] = {
                "group": ex["group"],
                "variants": [
                    {
                        "id": v["id"],
                        "label": VARIANT_LABELS.get(v["id"], {}).get(locale)
                        or VARIANT_LABELS.get(v["id"], {}).get("en")
                        or v["id"],
                        "fields": [
                            _field_to_api(FIELD_BY_ID[fid], locale) for fid in v["field_ids"]
                        ],
                    }
                    for v in ex["variants"]
                ],
            }
        sections_out.append(item)
    return {"sections": sections_out}


def _effective_values(cfg: Config) -> dict[str, Any]:
    return {
        "data_dir": str(cfg.data_dir),
        "log_level": cfg.log_level,
        "max_injection_chars": cfg.max_injection_chars,
        "disabled": cfg.disabled,
        "strict": cfg.strict,
        "dismiss_phrase": cfg.dismiss_phrase,
        "gate.enabled": cfg.gate_enabled,
        "gate.ttl_seconds": cfg.gate_ttl_seconds,
        "gate.matcher": cfg.gate_matcher,
        "extract.mode": cfg.extract_mode,
        "extract.defer_when_active": cfg.extract_defer_when_active,
        "llm.base_url": cfg.llm_base_url or "",
        "llm.model": cfg.llm_model or "",
        "llm.api_key": "***" if cfg.llm_api_key else "",
        "embed.enabled": cfg.embed_enabled,
        "embed.base_url": cfg.embed_base_url or "",
        "embed.model": cfg.embed_model or "",
        "embed.api_key": "***" if cfg.embed_api_key else "",
        "embed.dimensions": cfg.embed_dimensions,
        "embed.chunk_size": cfg.embed_chunk_size,
        "embed.chunk_count": cfg.embed_chunk_count,
        "embed.hook_timeout_seconds": cfg.embed_hook_timeout_seconds,
        "embed.server_idle_seconds": cfg.embed_server_idle_seconds,
        "embed.server_auto_start": cfg.embed_server_auto_start,
        "hot_cache.enabled": cfg.hot_cache_enabled,
        "session.idle_seconds": cfg.session_idle_seconds,
        "promotion.enabled": cfg.promotion_enabled,
    }


def _file_has_remote(doc: dict[str, Any]) -> bool:
    base = get_nested(doc, ("embed", "base_url"))
    model = get_nested(doc, ("embed", "model"))
    return bool(str(base or "").strip() and str(model or "").strip())


def infer_embed_mode(cfg: Config, doc: dict[str, Any]) -> EmbedMode:
    if _file_has_remote(doc):
        return "remote"
    if remote_embed_configured(cfg):
        return "remote"
    return "local"


def _display_value(
    field: FieldDef,
    *,
    set_in_file: bool,
    file_val: Any,
    effective: Any,
) -> Any:
    if field.id == "data_dir":
        return effective
    if field.field_type == "secret":
        if set_in_file:
            return None
        return ""
    if set_in_file:
        return file_val
    return field.default


def _env_locked_fields() -> set[str]:
    locked: set[str] = set()
    for field in FIELDS:
        if field.read_only:
            continue
        for env_name in env_keys_for_path(field.path):
            if os.environ.get(env_name) is not None:
                locked.add(field.id)
    return locked


def get_editor_state(cfg: Config, locale: str | None = None) -> dict[str, Any]:
    loc = _normalize_locale(locale)
    path = config_path(cfg.data_dir)
    doc = load_document(path)
    set_paths = list_set_paths(doc)
    effective = _effective_values(cfg)
    values: dict[str, Any] = {}
    secrets_set: list[str] = []

    for field in FIELDS:
        file_val = get_nested(doc, field.path)
        dot = ".".join(field.path)
        set_in_file = dot in set_paths
        if field.field_type == "secret" and set_in_file:
            secrets_set.append(field.id)
        values[field.id] = _display_value(
            field,
            set_in_file=set_in_file,
            file_val=file_val,
            effective=effective[field.id],
        )

    return {
        "config_path": str(path),
        "locale": loc,
        "schema": _schema_payload(loc),
        "values": values,
        "defaults": {f.id: f.default for f in FIELDS},
        "set_keys": sorted(set_paths),
        "secrets_set": secrets_set,
        "env_locked": sorted(_env_locked_fields()),
        "embed_mode": infer_embed_mode(cfg, doc),
        "exclusive_meta": {
            "embed_backend": {
                "local": {"clears_on_save": list(_REMOTE_CLEAR)},
                "remote": {"clears_on_save": list(_LOCAL_CLEAR)},
            },
        },
        "effective": effective,
    }


def _coerce_field(field: FieldDef, raw: Any) -> Any:
    if field.field_type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if field.field_type == "int":
        try:
            n = int(raw)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{field.id} must be an integer") from e
        if field.min_value is not None and n < field.min_value:
            raise ConfigError(f"{field.id} must be >= {field.min_value}")
        return n
    if field.field_type == "enum":
        s = str(raw).strip().lower()
        if field.options and s not in field.options:
            raise ConfigError(f"{field.id} must be one of {field.options}")
        return s
    if field.field_type in ("string", "secret"):
        if raw is None:
            return ""
        return str(raw).strip()
    raise ConfigError(f"unknown field type for {field.id}")


def _values_equal(field: FieldDef, a: Any, b: Any) -> bool:
    if field.field_type in ("string", "secret", "enum"):
        return str(a or "").strip() == str(b or "").strip()
    return a == b


def save_editor(
    cfg: Config,
    *,
    values: dict[str, Any],
    embed_mode: EmbedMode | None,
    initial_set_keys: set[str],  # TODO: implement optimistic concurrency check using initial_set_keys
) -> dict[str, Any]:
    path = config_path(cfg.data_dir)
    doc = load_document(path)
    current_set = list_set_paths(doc)
    sets: dict[str, Any] = {}
    removes: list[str] = []

    locked = _env_locked_fields()

    def _skip_for_embed_mode(field: FieldDef) -> bool:
        if embed_mode == "local" and field.id in _REMOTE_CLEAR:
            return True
        if embed_mode == "remote" and field.id in _LOCAL_CLEAR:
            return True
        return False

    for field in FIELDS:
        if field.read_only or field.id not in values:
            continue
        if field.id in locked:
            continue
        if _skip_for_embed_mode(field):
            continue
        raw = values[field.id]
        was_set = field.id in current_set or ".".join(field.path) in current_set

        if field.field_type == "secret":
            if raw is None or raw == "":
                continue
            sets[field.id] = _coerce_field(field, raw)
            continue

        coerced = _coerce_field(field, raw)
        default = field.default

        if _values_equal(field, coerced, default):
            if was_set:
                removes.append(field.id)
            continue

        if not was_set and _values_equal(field, coerced, default):
            continue

        sets[field.id] = coerced

    if embed_mode == "local":
        removes.extend(_REMOTE_CLEAR)
    elif embed_mode == "remote":
        removes.extend(_LOCAL_CLEAR)

    seen: set[str] = set()
    deduped_removes: list[str] = []
    for dot in removes:
        if dot not in seen:
            seen.add(dot)
            deduped_removes.append(dot)

    cfg.ensure_dirs()
    apply_patch(path, sets=sets, removes=deduped_removes)
    return {"saved": True, "config_path": str(path), "written_keys": sorted(sets.keys()), "removed_keys": deduped_removes}
