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


def read(
    cfg: Config,
    session_id: str,
    *,
    prompt_hash_value: str | None = None,
) -> Marker | None:
    """Read gate marker for this session and prompt turn (per-hash file)."""
    if not prompt_hash_value:
        return None
    return _load_marker_file(
        cfg.marker_path(session_id, prompt_hash_value), session_id
    )


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


def latest_marker_prompt_hash(cfg: Config, session_id: str) -> str | None:
    """Most recent prompt_hash from on-disk markers for this session."""
    mdir = cfg.marker_dir(session_id)
    if not mdir.is_dir():
        return None
    best_ph: str | None = None
    best_at = ""
    for path in mdir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ph = data.get("prompt_hash")
        if not ph:
            continue
        created = str(data.get("created_at") or "")
        if created >= best_at:
            best_at = created
            best_ph = str(ph)
    if best_ph:
        return best_ph
    try:
        newest = max(mdir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except ValueError:
        return None
    return newest.stem


def strip_short_id_from_all_markers(cfg: Config, short_id: str) -> int:
    """Remove a dismissed rule from gate markers (all sessions). Returns files touched."""
    root = cfg.data_dir / "gate_markers"
    if not root.is_dir():
        return 0
    needle = short_id.lower()
    touched = 0
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        for path in list(session_dir.glob("*.json")):
            marker = _load_marker_file(path, session_dir.name)
            if marker is None:
                continue
            kept = [r for r in marker.rules if r.short_id.lower() != needle]
            if len(kept) == len(marker.rules):
                continue
            touched += 1
            if not kept:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            payload = {
                "session_id": marker.session_id,
                "prompt_hash": marker.prompt_hash,
                "created_at": marker.created_at,
                "rules": [asdict(r) for r in kept],
            }
            _atomic_write_json(path, payload)
    return touched


def delete_session(cfg: Config, session_id: str) -> None:
    """Remove all gate markers for a session."""
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


def read_latest_marker(cfg: Config, session_id: str) -> Marker | None:
    ph = latest_marker_prompt_hash(cfg, session_id)
    if not ph:
        return None
    return read(cfg, session_id, prompt_hash_value=ph)


def injection_exists(db: Db, session_id: str, ph: str) -> bool:
    row = db.fetchone(
        "SELECT 1 FROM injections WHERE session_id = ? AND prompt_hash = ? LIMIT 1",
        (session_id, ph),
    )
    return row is not None


def resolve_current_prompt_hash(
    payload: dict,
    cfg: Config,
    session_id: str,
    *,
    db: Db | None = None,
) -> str | None:
    """Best-effort hash for the active user turn (PreToolUse has no prompt field)."""
    text = payload.get("prompt")
    if isinstance(text, str) and text:
        return prompt_hash(text)
    ph = latest_marker_prompt_hash(cfg, session_id)
    if ph and db is not None and injection_exists(db, session_id, ph):
        return ph
    if db is None:
        return None
    row = db.fetchone(
        "SELECT prompt_hash FROM injections WHERE session_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    )
    if row and row["prompt_hash"]:
        return str(row["prompt_hash"])
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
    if ttl_seconds <= 0:
        return False
    if not marker.created_at:
        return True
    created = parse_iso(marker.created_at)
    if created is None:
        return True
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age > ttl_seconds
