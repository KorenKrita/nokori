from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..db import Db
from ..extract.reader import read as read_transcript

HOT_CACHE_BUDGET_CHARS = 500
HOT_CACHE_RECENT_TURNS = 3


def _resolve_transcript_path(payload: dict) -> Path | None:
    candidate = payload.get("transcript_path") or payload.get("transcript")
    if candidate:
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def maybe_inject(payload: dict, cfg: Config, db: Db) -> str | None:
    """Return cache text if the previous transcript hasn't been extracted yet."""
    path = _resolve_transcript_path(payload)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    row = db.fetchone(
        "SELECT extracted_at FROM extract_state WHERE transcript_path = ?",
        (str(path),),
    )
    if row is not None:
        try:
            extracted = datetime.fromisoformat(row["extracted_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            extracted = None
        if extracted and extracted.timestamp() >= mtime:
            return None

    turns = read_transcript(path)
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
    return "".join(parts)


def mark_extracted(db: Db, path: Path, mtime: float) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO extract_state (transcript_path, transcript_mtime, "
            "extracted_at, status) VALUES (?, ?, ?, 'done') "
            "ON CONFLICT(transcript_path) DO UPDATE SET "
            "transcript_mtime = excluded.transcript_mtime, "
            "extracted_at = excluded.extracted_at, status = excluded.status",
            (str(path), mtime, now),
        )
