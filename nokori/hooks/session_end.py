from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..extract import jobs as job_io
from ..extract.reader import stat as transcript_stat
from ..utils import sessions
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.session_end")


def _resolve_project_id(payload: dict) -> str | None:
    cwd = payload.get("cwd")
    if not cwd:
        return None
    return cwd.rstrip("/").split("/")[-1] or None


def _resolve_transcript(payload: dict) -> Path | None:
    candidate = payload.get("transcript_path") or payload.get("transcript")
    if candidate:
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    sessions.end(cfg, session_id)

    transcript = _resolve_transcript(payload)
    if transcript is None:
        return {"continue": True}

    meta = transcript_stat(transcript)
    project_id = _resolve_project_id(payload)
    job_io.write_job(cfg, transcript, project_id, meta.mtime)
    log.info("queued extract job session=%s transcript=%s", session_id, transcript.name)
    return {"continue": True}
