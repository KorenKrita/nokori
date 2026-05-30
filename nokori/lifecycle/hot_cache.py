from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import Config
from ..db import Db
from ..extract.reader import read as read_transcript
from ..utils.time import now_iso

HOT_CACHE_BUDGET_CHARS = 500
HOT_CACHE_RECENT_TURNS = 3


def _resolve_transcript_path(payload: dict) -> Path | None:
    candidate = payload.get("transcript_path") or payload.get("transcript")
    if candidate:
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def _transcript_db_keys(path: Path) -> tuple[str, ...]:
    """Keys used in extract_state; tolerate absolute vs resolved paths."""
    resolved = path.expanduser().resolve()
    keys = (str(path), str(resolved))
    return tuple(dict.fromkeys(keys))


def find_previous_transcript(current: Path) -> Path | None:
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
    for candidate in parent.glob("*.jsonl"):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == current:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime >= current_mtime:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best = resolved
    return best


def _fetch_extract_state(db: Db, path: Path):
    for key in _transcript_db_keys(path):
        row = db.fetchone(
            "SELECT extracted_at, transcript_mtime, status "
            "FROM extract_state WHERE transcript_path = ?",
            (key,),
        )
        if row is not None:
            return row
    return None


def _was_extracted(db: Db, path: Path) -> bool:
    """True when this transcript was already extracted at its current revision."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return True

    row = _fetch_extract_state(db, path)
    if row is None:
        return False

    if row["status"] != "done":
        return False

    try:
        stored_mtime = float(row["transcript_mtime"])
    except (TypeError, ValueError):
        stored_mtime = 0.0
    if stored_mtime >= mtime - 1e-3:
        return True

    extracted_at = row["extracted_at"]
    if not extracted_at:
        return False
    try:
        extracted = datetime.fromisoformat(str(extracted_at).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True

    return extracted.timestamp() >= mtime


def maybe_inject(payload: dict, cfg: Config, db: Db) -> str | None:
    """Inject tail user messages from the previous session if not yet extracted."""
    current = _resolve_transcript_path(payload)
    if current is None:
        return None

    previous = find_previous_transcript(current)
    if previous is None:
        return None

    if _was_extracted(db, previous):
        return None

    turns = read_transcript(previous)
    user_turns = [t for t in turns if t.role == "human"]
    if not user_turns:
        return None
    tail = user_turns[-HOT_CACHE_RECENT_TURNS:]

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
    key = str(path.expanduser().resolve())
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, "
            "extracted_at, status) VALUES (?, ?, ?, 'done') "
            "ON CONFLICT(transcript_path) DO UPDATE SET "
            "transcript_mtime = excluded.transcript_mtime, "
            "extracted_at = excluded.extracted_at, status = excluded.status",
            (key, mtime, now),
        )
