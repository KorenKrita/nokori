from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..extract import jobs as job_io
from ..extract.reader import stat as transcript_stat
from ..utils import sessions
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id

log = get_logger("nokori.hooks.session_end")


def _resolve_transcript(payload: dict) -> Path | None:
    candidate = payload.get("transcript_path") or payload.get("transcript")
    if candidate:
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def _spawn_async_extract(cfg: Config) -> None:
    """Fork a detached subprocess to run `nokori extract`. Best-effort."""
    env = os.environ.copy()
    # Do not set NOKORI_EXTRACTING here — that guard is for hook recursion only
    # (see LLMAdapter); the extract CLI must be able to call the configured LLM.
    env.pop("NOKORI_EXTRACTING", None)
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "nokori", "extract"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.warning("async extract spawn failed: %s", e)


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    sessions.end(cfg, session_id)

    transcript = _resolve_transcript(payload)
    if transcript is None:
        return {"continue": True}

    meta = transcript_stat(transcript)
    project_id = resolve_project_id(payload.get("cwd"))
    job_io.write_job(cfg, transcript, project_id, meta.mtime)
    log.info("queued extract job session=%s transcript=%s", session_id, transcript.name)

    if cfg.extract_mode == "async":
        _spawn_async_extract(cfg)

    return {"continue": True}
