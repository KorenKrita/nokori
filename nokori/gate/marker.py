from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..db import Db
from ..utils.logging import get_logger
from ..utils.time import now_iso, parse_iso

log = get_logger("nokori.gate.marker")


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


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def write(
    cfg: Config,
    session_id: str,
    prompt: str,
    rules: list[MarkerRule],
    *,
    ph: str | None = None,
) -> Path:
    cfg.ensure_dirs()
    ph = ph if ph is not None else prompt_hash(prompt)
    payload = {
        "session_id": session_id,
        "prompt_hash": ph,
        "created_at": now_iso(),
        "rules": [asdict(r) for r in rules],
    }
    path = cfg.marker_path(session_id, ph)
    _atomic_write_json(path, payload)
    return path


def _load_marker_file(path: Path, session_id: str) -> Marker | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("malformed marker at %s: %s", path, e)
        try:
            path.unlink()
        except OSError:
            pass
        return None
    rules: list[MarkerRule] = []
    for r in data.get("rules", []):
        if not isinstance(r, dict):
            continue
        try:
            rules.append(MarkerRule(**r))
        except TypeError as e:
            log.warning("skip malformed marker rule in %s: %s", path, e)
    return Marker(
        session_id=data.get("session_id", session_id),
        prompt_hash=data.get("prompt_hash", ""),
        created_at=data.get("created_at", ""),
        rules=rules,
    )


def prune_stale_markers(cfg: Config, session_id: str, current_ph: str) -> None:
    """Drop markers for other prompt turns once the active hash is known."""
    mdir = cfg.marker_dir(session_id)
    if mdir.is_dir():
        for path in mdir.glob("*.json"):
            if path.stem != current_ph:
                try:
                    path.unlink()
                except OSError:
                    pass
    legacy = cfg.legacy_marker_path(session_id)
    if legacy.exists():
        old = _load_marker_file(legacy, session_id)
        if old is None or old.prompt_hash != current_ph:
            try:
                legacy.unlink()
            except OSError:
                pass


def read(
    cfg: Config,
    session_id: str,
    *,
    prompt_hash_value: str | None = None,
) -> Marker | None:
    """Read gate marker for this session and prompt turn (per-hash file)."""
    if prompt_hash_value:
        marker = _load_marker_file(
            cfg.marker_path(session_id, prompt_hash_value), session_id
        )
        if marker is not None:
            return marker
    legacy = _load_marker_file(cfg.legacy_marker_path(session_id), session_id)
    if legacy is None:
        return None
    if prompt_hash_value and legacy.prompt_hash != prompt_hash_value:
        return None
    return legacy


def delete(
    cfg: Config,
    session_id: str,
    *,
    prompt_hash_value: str | None = None,
) -> None:
    if prompt_hash_value:
        try:
            cfg.marker_path(session_id, prompt_hash_value).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        return
    delete_session(cfg, session_id)


def delete_session(cfg: Config, session_id: str) -> None:
    """Remove all gate markers for a session (legacy single file + per-hash dir)."""
    try:
        cfg.legacy_marker_path(session_id).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
    mdir = cfg.marker_dir(session_id)
    if not mdir.is_dir():
        return
    for path in mdir.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        mdir.rmdir()
    except OSError:
        pass


def resolve_current_prompt_hash(
    payload: dict,
    db: Db,
    session_id: str,
    *,
    marker: Marker | None = None,
) -> str | None:
    """Best-effort hash for the active user turn (PreToolUse has no prompt field)."""
    for key in ("prompt", "user_prompt"):
        text = payload.get(key)
        if isinstance(text, str) and text:
            return prompt_hash(text)
    row = db.fetchone(
        "SELECT prompt_hash FROM injections WHERE session_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    )
    if row and row["prompt_hash"]:
        return str(row["prompt_hash"])
    if marker and marker.prompt_hash:
        return marker.prompt_hash
    return None


def prompt_hash_matches(
    marker: Marker,
    current_ph: str | None,
    *,
    session_id: str | None = None,
) -> bool:
    """False when unknown or stale — caller should fail-open (no block)."""
    if not marker.prompt_hash:
        return False
    if not current_ph:
        if session_id:
            log.info(
                "gate prompt_hash unknown, fail-open session=%s", session_id
            )
        return False
    if marker.prompt_hash != current_ph:
        if session_id:
            log.info(
                "gate prompt_hash stale session=%s marker=%s current=%s",
                session_id, marker.prompt_hash[:8], current_ph[:8],
            )
        return False
    return True


def is_expired(marker: Marker, ttl_seconds: int) -> bool:
    if not marker.created_at:
        return True
    created = parse_iso(marker.created_at)
    if created is None:
        return True
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age > ttl_seconds
