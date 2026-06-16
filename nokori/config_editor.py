"""Config editor: merge user file values, defaults, and save semantics.

Also hosts the low-level config.toml read/write utilities (formerly config_file.py).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from .config import _CONFIG_FILE_NAME, _TOML_TO_ENV, Config
from .config_schema import (
    FIELD_BY_ID,
    FIELDS,
    SECTION_LABELS,
    SECTIONS,
    VARIANT_LABELS,
    FieldDef,
    Locale,
)
from .errors import ConfigError
from .search.embedding import remote_embed_configured

# ---------------------------------------------------------------------------
# Low-level config.toml read/write (was config_file.py)
# ---------------------------------------------------------------------------

_TOP_LEVEL_ORDER = (
    "log_level",
    "max_injection_chars",
    "disabled",
    "strict",
    "dismiss_phrase",
)
_SECTION_ORDER = ("gate", "extract", "llm", "embed", "hot_cache", "session", "promotion", "models")


def config_path(data_dir: Path) -> Path:
    return data_dir / _CONFIG_FILE_NAME


def load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_nested(doc: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def set_nested(doc: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    if len(path) == 1:
        doc[path[0]] = value
        return
    section = path[0]
    doc.setdefault(section, {})
    if not isinstance(doc[section], dict):
        doc[section] = {}
    rest = path[1:]
    if len(rest) == 1:
        doc[section][rest[0]] = value
    else:
        set_nested(doc[section], rest, value)


def del_nested(doc: dict[str, Any], path: tuple[str, ...]) -> None:
    if len(path) == 1:
        doc.pop(path[0], None)
        return
    section = path[0]
    if section not in doc or not isinstance(doc[section], dict):
        return
    if len(path) == 2:
        doc[section].pop(path[1], None)
        if not doc[section]:
            doc.pop(section, None)
    else:
        del_nested(doc[section], path[1:])
        if section in doc and isinstance(doc[section], dict) and not doc[section]:
            doc.pop(section, None)


def list_set_paths(doc: dict[str, Any], prefix: tuple[str, ...] = ()) -> set[str]:
    out: set[str] = set()
    for key, val in doc.items():
        path = (*prefix, key)
        dot = ".".join(path)
        if isinstance(val, dict):
            out.update(list_set_paths(val, path))
        else:
            out.add(dot)
    return out


def env_keys_for_path(path: tuple[str, ...]) -> list[str]:
    return [env for keys, env in _TOML_TO_ENV.items() if keys == path]


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        for ch, esc in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
            escaped = escaped.replace(ch, esc)
        return f'"{escaped}"'
    if isinstance(value, list):
        items = ", ".join(_format_toml_value(v) for v in value)
        return f"[{items}]"
    raise ConfigError(f"unsupported config value type: {type(value).__name__}")


def write_document(path: Path, doc: dict[str, Any]) -> None:
    lines: list[str] = []
    for key in _TOP_LEVEL_ORDER:
        if key in doc:
            lines.append(f"{key} = {_format_toml_value(doc[key])}")
    for extra in sorted(doc.keys()):
        if extra in _TOP_LEVEL_ORDER or extra in _SECTION_ORDER:
            continue
        if isinstance(doc[extra], dict):
            continue
        lines.append(f"{extra} = {_format_toml_value(doc[extra])}")

    for section in _SECTION_ORDER:
        block = doc.get(section)
        if not isinstance(block, dict) or not block:
            continue
        lines.append("")
        lines.append(f"[{section}]")
        sub_tables: list[tuple[str, dict]] = []
        for sk in sorted(block.keys()):
            if isinstance(block[sk], dict):
                sub_tables.append((sk, block[sk]))
            else:
                lines.append(f"{sk} = {_format_toml_value(block[sk])}")
        for sub_name, sub_block in sub_tables:
            if not sub_block:
                continue
            lines.append("")
            lines.append(f"[{section}.{sub_name}]")
            for k in sorted(sub_block.keys()):
                # ponytail: max 3-level nesting — deeper dicts silently skipped
                if isinstance(sub_block[k], dict):
                    continue
                lines.append(f"{k} = {_format_toml_value(sub_block[k])}")

    for extra in sorted(doc.keys()):
        if extra in _TOP_LEVEL_ORDER or extra in _SECTION_ORDER:
            continue
        if not isinstance(doc[extra], dict) or not doc[extra]:
            continue
        lines.append("")
        lines.append(f"[{extra}]")
        extra_sub_tables: list[tuple[str, dict]] = []
        for k in sorted(doc[extra].keys()):
            if isinstance(doc[extra][k], dict):
                extra_sub_tables.append((k, doc[extra][k]))
            else:
                lines.append(f"{k} = {_format_toml_value(doc[extra][k])}")
        for sub_name, sub_block in extra_sub_tables:
            if not sub_block:
                continue
            lines.append("")
            lines.append(f"[{extra}.{sub_name}]")
            for k in sorted(sub_block.keys()):
                if isinstance(sub_block[k], dict):
                    continue
                lines.append(f"{k} = {_format_toml_value(sub_block[k])}")

    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).strip() + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def apply_patch(
    path: Path,
    *,
    sets: dict[str, Any],
    removes: list[str],
) -> None:
    doc = load_document(path)
    for dot in removes:
        parts = tuple(dot.split("."))
        del_nested(doc, parts)
    for dot, value in sets.items():
        parts = tuple(dot.split("."))
        set_nested(doc, parts, value)
    write_document(path, doc)


# ---------------------------------------------------------------------------
# Config editor logic
# ---------------------------------------------------------------------------

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
        "label": (field.label or {}).get(locale) or (field.label or {}).get("en") or field.id,
        "description": (field.description or {}).get(locale) or (field.description or {}).get("en") or "",
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
        "extract.fork_cache": cfg.extract_fork_cache,
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
        # Per-role model configuration
        "models.extractor": cfg.role_models.get("extractor", ""),
        "models.admission_judge": cfg.role_models.get("admission_judge", ""),
        "models.rule_rewriter": cfg.role_models.get("rule_rewriter", ""),
        "models.final_judge": cfg.role_models.get("final_judge", ""),
        "models.merge_planner": cfg.role_models.get("merge_planner", ""),
        "models.synthetic_eval_generator": cfg.role_models.get("synthetic_eval_generator", ""),
        "models.posthoc_evaluator": cfg.role_models.get("posthoc_evaluator", ""),
        "models.limits.extractor_max_tokens": cfg.role_max_tokens.get("extractor", 4000),
        "models.limits.admission_judge_max_tokens": cfg.role_max_tokens.get(
            "admission_judge", 2000
        ),
        "models.limits.rule_rewriter_max_tokens": cfg.role_max_tokens.get("rule_rewriter", 4000),
        "models.limits.final_judge_max_tokens": cfg.role_max_tokens.get("final_judge", 2000),
        "models.limits.merge_planner_max_tokens": cfg.role_max_tokens.get("merge_planner", 3000),
        "models.limits.synthetic_eval_generator_max_tokens": cfg.role_max_tokens.get(
            "synthetic_eval_generator", 4000
        ),
        "models.limits.posthoc_evaluator_max_tokens": cfg.role_max_tokens.get(
            "posthoc_evaluator", 3000
        ),
        "models.timeouts.extractor_timeout": cfg.role_timeouts.get("extractor", 60),
        "models.timeouts.admission_judge_timeout": cfg.role_timeouts.get("admission_judge", 30),
        "models.timeouts.rule_rewriter_timeout": cfg.role_timeouts.get("rule_rewriter", 60),
        "models.timeouts.final_judge_timeout": cfg.role_timeouts.get("final_judge", 30),
        "models.timeouts.merge_planner_timeout": cfg.role_timeouts.get("merge_planner", 45),
        "models.timeouts.synthetic_eval_generator_timeout": cfg.role_timeouts.get(
            "synthetic_eval_generator", 60
        ),
        "models.timeouts.posthoc_evaluator_timeout": cfg.role_timeouts.get("posthoc_evaluator", 45),
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
    return bool(a == b)


def save_editor(
    cfg: Config,
    *,
    values: dict[str, Any],
    embed_mode: EmbedMode | None,
    initial_set_keys: set[str],
) -> dict[str, Any]:
    path = config_path(cfg.data_dir)
    doc = load_document(path)
    current_set = list_set_paths(doc)
    if current_set != set(initial_set_keys):
        raise ConfigError("config changed since it was loaded; refresh before saving")
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
        file_val = get_nested(doc, field.path)

        if _values_equal(field, coerced, default):
            if was_set:
                # Remove only if user actively changed from a non-default file value.
                # If file_val == coerced (both are default/empty), nothing changed — skip.
                try:
                    file_coerced = _coerce_field(field, file_val)
                except ConfigError:
                    # Coercion failed — treat file value as non-default (don't add to removals)
                    pass
                else:
                    if file_val is not None and not _values_equal(field, file_coerced, default):
                        removes.append(field.id)
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
    return {
        "saved": True,
        "config_path": str(path),
        "written_keys": sorted(sets.keys()),
        "removed_keys": deduped_removes,
    }
