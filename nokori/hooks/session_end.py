from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..config import Config
from ..extract import jobs as job_io
from ..extract.lock import is_locked
from ..extract.reader import stat as transcript_stat
from ..lifecycle import transcript_index
from ..utils import sessions
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id
from ..utils.transcript import resolve_transcript_path

log = get_logger("nokori.hooks.session_end")


def _spawn_async_extract(cfg: Config) -> None:
    """Fork a detached subprocess to run `nokori extract`. Best-effort."""
    env = os.environ.copy()
    # Do not set NOKORI_EXTRACTING here — that guard is for hook recursion only
    # (see LLMAdapter); the extract CLI must be able to call the configured LLM.
    env.pop("NOKORI_EXTRACTING", None)
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)
    cfg.ensure_dirs()
    err_log = cfg.logs_dir / "async-extract.log"
    err_fh = subprocess.DEVNULL
    try:
        err_fh = open(err_log, "a", encoding="utf-8")
    except OSError as e:
        log.warning("async extract log open failed: %s", e)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "nokori", "extract"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
            start_new_session=True,
        )
    except Exception as e:
        log.warning("async extract spawn failed: %s", e)
    finally:
        if err_fh is not subprocess.DEVNULL:
            try:
                err_fh.close()
            except OSError:
                pass


def handle(payload: dict, cfg: Config) -> dict:
    session_id = payload.get("session_id") or "-"
    sessions.end(cfg, session_id)

    transcript = resolve_transcript_path(payload)
    if transcript is None:
        return {"continue": True}

    transcript_index.record_session_transcript(cfg, transcript)
    meta = transcript_stat(transcript)
    project_id = sessions.resolve_project_id_for_session(
        cfg, session_id, payload.get("cwd"), resolve_fn=resolve_project_id,
    )
    job_io.write_job(cfg, transcript, project_id, meta.mtime)
    log.info("queued extract job session=%s transcript=%s", session_id, transcript.name)

    if cfg.extract_mode == "async":
        if cfg.extract_defer_when_active:
            others = sessions.count_open_sessions(
                cfg, exclude_session=session_id
            )
            if others > 0:
                log.info(
                    "deferred async extract: %d other open session(s); "
                    "job queued — run `nokori extract` when idle",
                    others,
                )
            elif not is_locked(cfg):
                _spawn_async_extract(cfg)
            else:
                log.info(
                    "async extract skipped: extract already running "
                    "(pending jobs remain)"
                )
        elif not is_locked(cfg):
            _spawn_async_extract(cfg)
        else:
            log.info(
                "async extract skipped: extract already running "
                "(pending jobs remain)"
            )

    return {"continue": True}
