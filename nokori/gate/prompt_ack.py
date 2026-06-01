"""File-based record that UserPromptSubmit ran for a user turn (no DB schema changes)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Config
from ..utils.fs import atomic_write_json
from ..utils.time import now_iso, parse_iso

_DEFAULT_STALE_HOURS = 24


def _ack_dir(cfg: Config, session_id: str) -> Path:
    return cfg.data_dir / "prompt_submit_ack" / cfg._safe_session_id(session_id)


def _ack_path(cfg: Config, session_id: str, prompt_hash: str) -> Path:
    return _ack_dir(cfg, session_id) / f"{prompt_hash}.json"


def record(cfg: Config, session_id: str, prompt_hash: str) -> None:
    """Mark that UserPromptSubmit / beforeSubmitPrompt ran for this prompt hash."""
    if not session_id or session_id == "-" or not prompt_hash:
        return
    cfg.ensure_dirs()
    atomic_write_json(
        _ack_path(cfg, session_id, prompt_hash),
        {"session_id": session_id, "prompt_hash": prompt_hash, "recorded_at": now_iso()},
    )


def exists(cfg: Config, session_id: str, prompt_hash: str) -> bool:
    if not session_id or session_id == "-" or not prompt_hash:
        return False
    return _ack_path(cfg, session_id, prompt_hash).is_file()


def _deferred_dir(cfg: Config, session_id: str) -> Path:
    return cfg.data_dir / "cursor_deferred" / cfg._safe_session_id(session_id)


def _deferred_path_generation(
    cfg: Config, session_id: str, generation_id: str, prompt_hash: str
) -> Path:
    """One deferred-inject per (generation_id, prompt_hash) within a session."""
    safe_gen = cfg._safe_session_id(generation_id)
    safe_ph = cfg._safe_session_id(prompt_hash)
    return _deferred_dir(cfg, session_id) / f"{safe_gen}_{safe_ph}.json"


def _deferred_path_prompt_only(cfg: Config, session_id: str, prompt_hash: str) -> Path:
    """Deferred dedup when Cursor sends no generation_id (per user turn hash)."""
    return _deferred_dir(cfg, session_id) / f"{cfg._safe_session_id(prompt_hash)}.json"


def _deferred_path(
    cfg: Config, session_id: str, generation_id: str, prompt_hash: str
) -> Path:
    if generation_id:
        return _deferred_path_generation(cfg, session_id, generation_id, prompt_hash)
    return _deferred_path_prompt_only(cfg, session_id, prompt_hash)


def deferred_done(
    cfg: Config, session_id: str, generation_id: str, prompt_hash: str
) -> bool:
    if not session_id or session_id == "-" or not prompt_hash:
        return False
    return _deferred_path(cfg, session_id, generation_id, prompt_hash).is_file()


def try_claim_deferred(
    cfg: Config, session_id: str, generation_id: str, prompt_hash: str
) -> bool:
    """Atomically claim deferred inject for this turn (parallel preToolUse safe).

    Returns True only for the first claimant; others must skip deferred inject.
    """
    if not session_id or session_id == "-" or not prompt_hash:
        return False
    path = _deferred_path(cfg, session_id, generation_id, prompt_hash)
    if path.is_file():
        return False
    cfg.ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "session_id": session_id,
        "prompt_hash": prompt_hash,
        "recorded_at": now_iso(),
    }
    if generation_id:
        payload["generation_id"] = generation_id
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
    except FileExistsError:
        return False
    return True


def mark_deferred_done(
    cfg: Config, session_id: str, generation_id: str, *, prompt_hash: str
) -> None:
    if not session_id or session_id == "-" or not prompt_hash:
        return
    if deferred_done(cfg, session_id, generation_id, prompt_hash):
        return
    try_claim_deferred(cfg, session_id, generation_id, prompt_hash)


def _prune_json_tree(root: Path, cutoff: datetime) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.glob("*/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        recorded = parse_iso(data.get("recorded_at"))
        if recorded is None or recorded < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    for session_dir in root.iterdir():
        if session_dir.is_dir() and not any(session_dir.glob("*.json")):
            try:
                session_dir.rmdir()
            except OSError:
                pass
    return removed


def cleanup_session(cfg: Config, session_id: str) -> int:
    """Remove ack/deferred files for one session (call at session end)."""
    if not session_id or session_id == "-":
        return 0
    removed = 0
    for root in (_ack_dir(cfg, session_id), _deferred_dir(cfg, session_id)):
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        try:
            root.rmdir()
        except OSError:
            pass
    return removed


def prune_stale(cfg: Config, max_age_hours: int = _DEFAULT_STALE_HOURS) -> int:
    """Drop ack/deferred files older than max_age_hours (orphaned sessions)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0
    for name in ("prompt_submit_ack", "cursor_deferred"):
        removed += _prune_json_tree(cfg.data_dir / name, cutoff)
    return removed
