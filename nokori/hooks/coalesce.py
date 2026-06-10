"""Idempotent hook claims when Claude + Cursor both register the same events."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Config
from ..gate.marker import prompt_hash
from ..utils.host import effective_session_id
from ..utils.logging import get_logger
from ..utils.prompt_text import normalize_prompt_for_hash
from ..utils.time import local_now, now_iso, parse_iso
from ..utils.transcript import resolve_transcript_path, transcript_key

log = get_logger("nokori.hooks.coalesce")

_TRUE = frozenset({"1", "true", "yes", "on"})
_DEFAULT_CLAIM_MAX_AGE_HOURS = 24


def coalesce_enabled() -> bool:
    raw = os.environ.get("NOKORI_HOOK_COALESCE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def claim_key_for_event(cli_event: str, payload: dict) -> str | None:
    """Stable idempotency key per hook invocation, or None to skip coalesce.

  pre-tool-use: intentionally None — gate uses a single on-disk marker; the first
  process deletes it after deny, so a parallel second hook sees no marker and
  pass-throughs (cursor deferred uses try_claim_deferred separately).

  session-end: key is transcript path; Claude (~/.claude) vs Cursor (~/.cursor)
  paths differ, so cross-platform dedup does not apply. Coalesce only helps when
  the same platform fires session-end twice; extract defer is idempotent via
  sessions.end() and job files.
    """
    session_id = effective_session_id(payload)
    if cli_event == "session-start":
        return f"session-start|{session_id}"
    if cli_event == "user-prompt-submit":
        prompt = payload.get("prompt") or ""
        normalized = normalize_prompt_for_hash(prompt)
        ph = prompt_hash(normalized) if normalized else ""
        if not ph:
            return None
        # session_id + prompt_hash only — generation_id differs across hosts.
        return f"user-prompt-submit|{session_id}|{ph}"
    if cli_event == "session-end":
        path = resolve_transcript_path(payload)
        if path is None:
            return None
        return f"session-end|{transcript_key(path)}"
    return None


def _claim_path(cfg: Config, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return cfg.data_dir / "hook_coalesce" / f"{digest}.json"


def try_claim(cfg: Config, key: str, *, cli_event: str = "") -> bool:
    """Return True if this process won the claim (should run hook logic)."""
    if not coalesce_enabled():
        return True
    path = _claim_path(cfg, key)
    if path.is_file():
        return False
    cfg.ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "cli_event": cli_event,
        "claimed_at": now_iso(),
        "pid": os.getpid(),
    }
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
    except FileExistsError:
        return False
    return True


def prune_stale_claims(cfg: Config, max_age_hours: int = _DEFAULT_CLAIM_MAX_AGE_HOURS) -> int:
    """Remove hook_coalesce claim files older than max_age_hours."""
    root = cfg.data_dir / "hook_coalesce"
    if not root.is_dir():
        return 0
    cutoff = local_now() - timedelta(hours=max_age_hours)
    removed = 0
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        claimed = parse_iso(data.get("claimed_at"))
        if claimed is None or claimed < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def duplicate_passthrough(cli_event: str, host) -> dict:
    """Safe empty response when a duplicate hook invocation is suppressed."""
    from ..utils.hook_response import (
        session_start_response,
        user_prompt_submit_response,
    )
    log.info("duplicate hook suppressed cli_event=%s host=%s", cli_event, host.value)
    if cli_event == "session-start":
        return session_start_response(host, None)
    if cli_event == "user-prompt-submit":
        return user_prompt_submit_response(host, None)
    if cli_event == "session-end":
        return {"continue": True}
    if cli_event == "pre-tool-use":
        return {}
    return {"continue": True}
