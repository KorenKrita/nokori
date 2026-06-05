from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import Config
from ..utils.fs import atomic_write_json
from ..utils.logging import get_logger

log = get_logger("nokori.lifecycle.transcript_index")


def _parent_key(parent: Path) -> str:
    try:
        resolved = str(parent.expanduser().resolve())
    except OSError:
        resolved = str(parent)
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def _index_path(cfg: Config, parent: Path) -> Path:
    return cfg.data_dir / "transcript_index" / f"{_parent_key(parent)}.json"



def lookup_previous(cfg: Config, current: Path) -> Path | None:
    try:
        parent = current.parent.resolve()
        current_resolved = current.resolve()
    except OSError:
        return None

    path = _index_path(cfg, parent)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    prev = data.get("previous")
    if not isinstance(prev, dict):
        return None
    p = Path(prev.get("path") or "")
    if not p.exists():
        return None
    try:
        if p.resolve() == current_resolved:
            return None
    except OSError:
        return None
    return p
