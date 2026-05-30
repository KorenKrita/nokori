from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..config import Config
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


def record_session_transcript(cfg: Config, transcript: Path) -> None:
    """Remember current transcript as latest for this parent directory."""
    try:
        parent = transcript.parent.resolve()
        resolved = transcript.resolve()
        mtime = transcript.stat().st_mtime
    except OSError as e:
        log.warning("transcript_index skip %s: %s", transcript, e)
        return

    path = _index_path(cfg, parent)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}

    current = {"path": str(resolved), "mtime": mtime}
    prev = data.get("current")
    if prev and prev.get("path") != current["path"]:
        data["previous"] = prev
    data["current"] = current

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


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
