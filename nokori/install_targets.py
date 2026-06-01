"""Record which hook platforms were installed via `nokori install`."""
from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .utils.fs import atomic_write_json
from .utils.time import now_iso

PLATFORM_CLAUDE = "claude"
PLATFORM_CURSOR = "cursor"
ALL_PLATFORMS = (PLATFORM_CLAUDE, PLATFORM_CURSOR)


def targets_path(cfg: Config) -> Path:
    return cfg.data_dir / "install_targets.json"


def read_platforms(cfg: Config) -> list[str]:
    """Platforms recorded by `nokori install` (empty if never installed via CLI)."""
    path = targets_path(cfg)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("platforms")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if p in ALL_PLATFORMS]


def platforms_for_health(cfg: Config) -> list[str]:
    """Health checks: recorded platforms, or Claude-only if not yet recorded."""
    recorded = read_platforms(cfg)
    return recorded if recorded else [PLATFORM_CLAUDE]


def merge_platforms(cfg: Config, installed: list[str]) -> list[str]:
    current = set(read_platforms(cfg))
    current.update(p for p in installed if p in ALL_PLATFORMS)
    merged = sorted(current)
    write_platforms(cfg, merged)
    return merged


def remove_platforms(cfg: Config, removed: list[str]) -> list[str]:
    current = set(read_platforms(cfg))
    for p in removed:
        current.discard(p)
    merged = sorted(current)
    write_platforms(cfg, merged)
    return merged


def write_platforms(cfg: Config, platforms: list[str]) -> None:
    cfg.ensure_dirs()
    path = targets_path(cfg)
    payload = {
        "platforms": sorted({p for p in platforms if p in ALL_PLATFORMS}),
        "updated_at": now_iso(),
    }
    atomic_write_json(path, payload, indent=2)


def format_platforms_label(platforms: list[str]) -> str:
    return ",".join(platforms) if platforms else "(none)"
