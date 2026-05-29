from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..errors import GateMarkerError


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class MarkerRule:
    short_id: str
    action: str
    source_type: str
    rationale: str | None = None


@dataclass
class Marker:
    session_id: str
    prompt_hash: str
    created_at: str
    rules: list[MarkerRule]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write(cfg: Config, session_id: str, prompt: str, rules: list[MarkerRule]) -> Path:
    cfg.ensure_dirs()
    payload = {
        "session_id": session_id,
        "prompt_hash": prompt_hash(prompt),
        "created_at": _now_iso(),
        "rules": [asdict(r) for r in rules],
    }
    path = cfg.marker_path(session_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read(cfg: Config, session_id: str) -> Marker | None:
    path = cfg.marker_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GateMarkerError(f"malformed marker at {path}: {e}") from e
    rules = [MarkerRule(**r) for r in data.get("rules", [])]
    return Marker(
        session_id=data.get("session_id", session_id),
        prompt_hash=data.get("prompt_hash", ""),
        created_at=data.get("created_at", ""),
        rules=rules,
    )


def delete(cfg: Config, session_id: str) -> None:
    path = cfg.marker_path(session_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def is_expired(marker: Marker, ttl_seconds: int) -> bool:
    if not marker.created_at:
        return True
    try:
        created = datetime.fromisoformat(marker.created_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age > ttl_seconds
