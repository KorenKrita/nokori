"""Read/write ~/.nokori/config.toml for the web config editor."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from .config import _CONFIG_FILE_NAME, _TOML_TO_ENV
from .errors import ConfigError

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


def env_overrides() -> set[str]:
    return {name for name in os.environ if name.startswith("NOKORI_")}


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
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
