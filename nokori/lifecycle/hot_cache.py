from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import Config
from ..constants import TRANSCRIPT_MTIME_EPSILON_SEC
from ..db import Db
from ..extract.reader import read_tail_user_turns
from . import transcript_index
from ..utils.time import now_iso
from ..utils.transcript import is_path_allowed, resolve_transcript_path

HOT_CACHE_BUDGET_CHARS = 500
HOT_CACHE_RECENT_TURNS = 3


def _transcript_db_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def find_previous_transcript(current: Path, cfg: Config | None = None) -> Path | None:
    """Previous session transcript: O(1) index when available, else directory scan."""
    if cfg is not None:
        indexed = transcript_index.lookup_previous(cfg, current)
        if indexed is not None:
            try:
                resolved = indexed.expanduser().resolve()
            except OSError:
                return _find_previous_transcript_glob(current)
            if is_path_allowed(resolved):
                return indexed
    return _find_previous_transcript_glob(current)


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
    def _mtime_key(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    candidates = sorted(parent.glob("*.jsonl"), key=_mtime_key, reverse=True)
    for candidate in candidates[:50]:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not is_path_allowed(resolved):
            continue
        if resolved == current:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > current_mtime:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best = resolved
    return best


def _fetch_extract_state(db: Db, path: Path):
    return db.fetchone(
        "SELECT extracted_at, transcript_mtime, status "
        "FROM extract_state WHERE transcript_path = ?",
        (_transcript_db_key(path),),
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
    try:
        extracted = datetime.fromisoformat(str(extracted_at).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False

    return extracted.timestamp() >= mtime


def maybe_inject(payload: dict, cfg: Config, db: Db) -> str | None:
    """Inject tail user messages from the previous session if not yet extracted."""
    if not cfg.hot_cache_enabled:
        return None
    current = resolve_transcript_path(payload)
    if current is None:
        return None

    previous = find_previous_transcript(current, cfg)
    if previous is None:
        return None

    if _was_extracted(db, previous):
        return None

    tail = read_tail_user_turns(previous, HOT_CACHE_RECENT_TURNS)
    if not tail:
        return None

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
    if len(parts) == 1:
        return None
    return "".join(parts)


def mark_extracted(db: Db, path: Path, mtime: float) -> None:
    now = now_iso()
    key = _transcript_db_key(path)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, "
            "extracted_at, status) VALUES (?, ?, ?, 'done') "
            "ON CONFLICT(transcript_path) DO UPDATE SET "
            "transcript_mtime = excluded.transcript_mtime, "
            "extracted_at = excluded.extracted_at, status = excluded.status",
            (key, mtime, now),
        )
