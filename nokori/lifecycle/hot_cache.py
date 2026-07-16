from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import Config
from ..constants import TRANSCRIPT_MTIME_EPSILON_SEC
from ..db import Db
from ..extract.reader import read_tail_user_turns
from ..utils.time import local_days_ago, now_iso, parse_iso
from ..utils.transcript import is_path_allowed, resolve_transcript_path, transcript_key

HOT_CACHE_BUDGET_CHARS = 500
HOT_CACHE_RECENT_TURNS = 3
# Include trusted rules that fired in the last N days
TRUSTED_RECENT_WINDOW_DAYS = 7
_TRUSTED_RULES_BUDGET = 500

# Dir listing cache: invalidate when parent directory mtime changes.
# Maps resolved parent path -> (dir_mtime, sorted [(file_mtime, path), ...]).
_DIR_LISTING_CACHE: dict[str, tuple[float, list[tuple[float, Path]]]] = {}


def clear_dir_listing_cache() -> None:
    """Clear transcript directory listing cache (useful in tests)."""
    _DIR_LISTING_CACHE.clear()


def find_previous_transcript(current: Path) -> Path | None:
    """Previous session transcript via directory scan."""
    return _find_previous_transcript_glob(current)


def _list_jsonl_by_mtime(parent: Path) -> list[tuple[float, Path]]:
    """Return *.jsonl entries as (mtime, path), newest first, cached by dir mtime."""
    try:
        dir_mtime = parent.stat().st_mtime
    except OSError:
        return []

    cache_key = str(parent)
    cached = _DIR_LISTING_CACHE.get(cache_key)
    if cached is not None and cached[0] == dir_mtime:
        return cached[1]

    entries: list[tuple[float, Path]] = []
    try:
        for candidate in parent.glob("*.jsonl"):
            try:
                entries.append((candidate.stat().st_mtime, candidate))
            except OSError:
                continue
    except OSError:
        return []

    entries.sort(key=lambda t: t[0], reverse=True)
    _DIR_LISTING_CACHE[cache_key] = (dir_mtime, entries)
    return entries


def _find_previous_transcript_glob(current: Path) -> Path | None:
    """Pick the newest *.jsonl in the same directory with mtime strictly before *current*."""
    current = current.expanduser().resolve()
    if not current.is_file():
        return None
    try:
        current_mtime = current.stat().st_mtime
    except OSError:
        return None

    parent = current.parent
    if not parent.is_dir():
        return None

    best: Path | None = None
    best_mtime = -1.0

    for mtime, candidate in _list_jsonl_by_mtime(parent)[:50]:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not is_path_allowed(resolved):
            continue
        if resolved == current:
            continue
        if mtime > current_mtime:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best = resolved
    return best


def _fetch_extract_state(db: Db, path: Path) -> sqlite3.Row | None:
    return db.fetchone(
        "SELECT extracted_at, transcript_mtime, status, last_byte_offset "
        "FROM extract_state WHERE transcript_path = ?",
        (transcript_key(path),),
    )


def _was_extracted(db: Db, path: Path) -> bool:
    """True when this transcript was already extracted at its current revision."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False

    row = _fetch_extract_state(db, path)
    if row is None:
        return False

    if row["status"] != "done":
        return False

    try:
        stored_mtime = float(row["transcript_mtime"])
    except (TypeError, ValueError):
        stored_mtime = 0.0
    if stored_mtime >= mtime - TRANSCRIPT_MTIME_EPSILON_SEC:
        return True

    extracted_at = row["extracted_at"]
    if not extracted_at:
        return False
    extracted = parse_iso(str(extracted_at))
    if extracted is None:
        return False

    return extracted.timestamp() >= mtime


def _recent_trusted_rules_summary(db: Db) -> str | None:
    """Build a summary of recently fired active/trusted rules from rule_fire_events."""
    cutoff = local_days_ago(TRUSTED_RECENT_WINDOW_DAYS)

    rows = db.fetchall(
        "SELECT r.id, r.trigger_canonical, r.action_instruction, "
        "MAX(e.created_at) AS last_fired "
        "FROM rule_fire_events e "
        "JOIN rules r ON r.id = e.rule_id "
        "WHERE r.status IN ('active', 'trusted') "
        "AND e.created_at >= ? "
        "GROUP BY r.id "
        "ORDER BY last_fired DESC "
        "LIMIT 5",
        (cutoff,),
    )
    if not rows:
        return None

    parts = ["[Nokori hot-cache] recently active trusted rules:"]
    used = len(parts[0])
    for row in rows:
        trigger = (row["trigger_canonical"] or "").strip().replace("\n", " ")[:80]
        action = (row["action_instruction"] or "").strip().replace("\n", " ")[:80]
        content = f"{trigger} -> {action}" if trigger else action
        if content:
            line = f"\n- {content}"
            if used + len(line) > _TRUSTED_RULES_BUDGET:
                break
            parts.append(line)
            used += len(line)
    if len(parts) == 1:
        return None
    return "".join(parts)


def maybe_inject(payload: dict, cfg: Config, db: Db) -> str | None:
    """Inject tail user messages from the previous session if not yet extracted.

    Also includes a summary of recently injected trusted rules from
    rule_fire_events history.
    """
    if not cfg.hot_cache_enabled:
        return None
    current = resolve_transcript_path(payload)
    if current is None:
        return None

    sections: list[str] = []

    # Transcript context from previous session
    previous = find_previous_transcript(current)
    if previous is not None and not _was_extracted(db, previous):
        tail = read_tail_user_turns(previous, HOT_CACHE_RECENT_TURNS)
        if tail:
            parts = ["[Nokori hot-cache] last messages from the previous session:"]
            used = len(parts[0]) + 1
            for t in tail:
                msg = t.content.strip().replace("\n", " ")
                if not msg:
                    continue
                line = f"\n- {msg[:200]}"
                if used + len(line) > HOT_CACHE_BUDGET_CHARS:
                    break
                parts.append(line)
                used += len(line)
            if len(parts) > 1:
                sections.append("".join(parts))

    # Recently fired trusted rules summary
    trusted_summary = _recent_trusted_rules_summary(db)
    if trusted_summary:
        sections.append(trusted_summary)

    if not sections:
        return None
    return "\n\n".join(sections)


def load_last_byte_offset(db: Db, path: Path) -> int:
    """Return the byte offset up to which this transcript was last extracted."""
    row = _fetch_extract_state(db, path)
    if row is None:
        return 0
    try:
        return int(row["last_byte_offset"])
    except (TypeError, ValueError):
        return 0


def mark_extracted(db: Db, path: Path, mtime: float, byte_offset: int = 0) -> None:
    now = now_iso()
    key = transcript_key(path)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, "
            "extracted_at, status, last_byte_offset) VALUES (?, ?, ?, 'done', ?) "
            "ON CONFLICT(transcript_path) DO UPDATE SET "
            "transcript_mtime = excluded.transcript_mtime, "
            "extracted_at = excluded.extracted_at, status = excluded.status, "
            "last_byte_offset = excluded.last_byte_offset",
            (key, mtime, now, byte_offset),
        )
