from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from ..config import Config
from ..db import Db
from ..utils.fs import atomic_write_json
from ..utils.logging import get_logger
from ..utils.time import local_now, now_iso, parse_iso

log = get_logger("nokori.gate.marker")


class MarkerState(StrEnum):
    consumed = "consumed"  # marker matched, tool blocked
    expired = "expired"  # TTL exceeded at check time
    ineligible = "ineligible"  # all rules failed eligibility/evidence/exclusion checks
    hash_mismatch = "hash_mismatch"  # marker for different prompt turn
    empty = "empty"  # zero rules in marker
    error = "error"  # exception during processing


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class MarkerRule:
    short_id: str
    action: str
    trigger: str = ""
    source_type: str = "transcript_extraction"
    rationale: str | None = None
    rule_id: str | None = None
    status: str | None = None
    severity: str | None = None
    rule_version: int | None = None
    runtime_policy_version: str | None = None
    trigger_idf_pool_version: str | None = None
    embedding_profile_version: str | None = None
    decision_features: dict | None = None


@dataclass
class Marker:
    session_id: str
    prompt_hash: str
    created_at: str
    rules: list[MarkerRule]


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
    atomic_write_json(path, payload, mkdir=True)
    return path


def _load_marker_file(path: Path, session_id: str) -> Marker | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("malformed marker at %s: %s", path, e)
        with contextlib.suppress(OSError):
            path.unlink()
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
                with contextlib.suppress(OSError):
                    path.unlink()


def read(
    cfg: Config,
    session_id: str,
    *,
    prompt_hash_value: str | None = None,
) -> Marker | None:
    """Read gate marker for this session and prompt turn (per-hash file)."""
    if not prompt_hash_value:
        return None
    return _load_marker_file(cfg.marker_path(session_id, prompt_hash_value), session_id)


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
    """Most recent prompt_hash from on-disk markers for this session.

    Uses path.stem as the authoritative prompt_hash (filename is {ph}.json by
    construction in marker_path/write). The JSON body's created_at is only used
    for ordering.
    """
    mdir = cfg.marker_dir(session_id)
    if not mdir.is_dir():
        return None
    paths = list(mdir.glob("*.json"))
    best_ph: str | None = None
    best_at = ""
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        created = str(data.get("created_at") or "")
        if created >= best_at:
            best_at = created
            best_ph = path.stem
    if best_ph:
        return best_ph
    try:
        newest = max(paths, key=lambda p: p.stat().st_mtime)
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
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            payload = {
                "session_id": marker.session_id,
                "prompt_hash": marker.prompt_hash,
                "created_at": marker.created_at,
                "rules": [asdict(r) for r in kept],
            }
            atomic_write_json(path, payload, mkdir=True)
    return touched


def delete_session(cfg: Config, session_id: str) -> None:
    """Remove all gate markers for a session."""
    mdir = cfg.marker_dir(session_id)
    if not mdir.is_dir():
        return
    for path in mdir.glob("*.json"):
        with contextlib.suppress(OSError):
            path.unlink()
    with contextlib.suppress(OSError):
        mdir.rmdir()


def read_latest_marker(cfg: Config, session_id: str) -> Marker | None:
    ph = latest_marker_prompt_hash(cfg, session_id)
    if not ph:
        return None
    return read(cfg, session_id, prompt_hash_value=ph)


def injection_exists(db: Db, session_id: str, ph: str) -> bool:
    row = db.fetchone(
        "SELECT 1 FROM rule_fire_events WHERE session_id = ? AND prompt_hash = ? LIMIT 1",
        (session_id, ph),
    )
    return row is not None


class PromptHashResolver:
    """Three-layer prompt hash resolution with per-layer observability."""

    def __init__(self, cfg: Config, session_id: str, db: Db) -> None:
        self._cfg = cfg
        self._session_id = session_id
        self._db = db

    def resolve(self, payload: dict, on_disk_marker: Marker | None) -> tuple[str | None, str]:
        """Returns (prompt_hash, source_layer).

        source_layer: "payload" | "disk_marker" | "fire_events" | "none"

        Side-effect: deletes on-disk marker when injection_exists returns False
        (stale marker cleanup, preserves original _run_gate behavior).
        """
        from ..utils.prompt_text import normalize_prompt_for_hash

        # Layer 1: payload prompt field
        text = payload.get("prompt")
        if isinstance(text, str) and text.strip():
            ph = prompt_hash(normalize_prompt_for_hash(text))
            log.debug("prompt_hash resolved from payload session=%s", self._session_id)
            return ph, "payload"

        # Layer 2: on-disk marker with injection_exists check
        if on_disk_marker and on_disk_marker.rules:
            disk_ph = on_disk_marker.prompt_hash
            if disk_ph and injection_exists(self._db, self._session_id, disk_ph):
                log.debug(
                    "prompt_hash resolved from disk_marker session=%s ph=%s",
                    self._session_id,
                    disk_ph[:8],
                )
                return disk_ph, "disk_marker"
            else:
                log.debug(
                    "disk_marker present but no injection_exists session=%s ph=%s",
                    self._session_id,
                    disk_ph[:8] if disk_ph else "-",
                )
                if disk_ph:
                    delete(self._cfg, self._session_id, prompt_hash_value=disk_ph)

        # Layer 3: latest rule_fire_events row
        row = self._db.fetchone(
            "SELECT prompt_hash FROM rule_fire_events WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (self._session_id,),
        )
        if row and row["prompt_hash"]:
            ph = str(row["prompt_hash"])
            log.debug(
                "prompt_hash resolved from fire_events session=%s ph=%s",
                self._session_id,
                ph[:8],
            )
            return ph, "fire_events"

        log.debug("prompt_hash unresolved session=%s", self._session_id)
        return None, "none"


def resolve_current_prompt_hash(
    payload: dict,
    cfg: Config,
    session_id: str,
    *,
    db: Db | None = None,
) -> str | None:
    """Best-effort hash for the active user turn (PreToolUse has no prompt field).

    Backward-compat wrapper around PromptHashResolver.
    """
    if db is None:
        from ..utils.prompt_text import normalize_prompt_for_hash

        text = payload.get("prompt")
        if isinstance(text, str) and text.strip():
            return prompt_hash(normalize_prompt_for_hash(text))
        return None

    on_disk = read_latest_marker(cfg, session_id)
    resolver = PromptHashResolver(cfg, session_id, db)
    ph, _source = resolver.resolve(payload, on_disk)
    return ph


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
            log.info("gate prompt_hash unknown, fail-open session=%s", session_id)
        return False
    if marker.prompt_hash != current_ph:
        if session_id:
            log.info(
                "gate prompt_hash stale session=%s marker=%s current=%s",
                session_id,
                marker.prompt_hash[:8],
                current_ph[:8],
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
    age = (local_now() - created).total_seconds()
    return age > ttl_seconds
