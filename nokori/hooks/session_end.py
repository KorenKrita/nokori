from __future__ import annotations

import json
from pathlib import Path

from ..config import Config
from ..db import Db
from ..extract.jobs import write_job as write_extract_job
from ..gate import prompt_ack
from ..posthoc import enqueue_posthoc_for_session
from ..posthoc.windowing import compute_event_window, extract_window_content
from ..utils import sessions
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id
from ..utils.transcript import resolve_transcript_path
from .context import ErrorCategory, HotPathContext

log = get_logger("nokori.hooks.session_end")


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    if cfg.disabled:
        return {"continue": True}

    session_id = effective_session_id(payload)
    sessions.end(cfg, session_id)
    ack_removed = prompt_ack.cleanup_session(cfg, session_id)
    if ack_removed:
        log.info("cleaned prompt ack/deferred session=%s files=%d", session_id, ack_removed)

    posthoc_enqueued = False
    posthoc_failed = False

    with HotPathContext(payload, cfg, host=host, session_id=session_id) as ctx:
        # Posthoc requires DB; skip if unavailable
        if ctx.db is not None:
            try:
                session_turns = _extract_session_turns(payload)
                enqueue_posthoc_for_session(ctx.db, session_id)
                posthoc_enqueued = True
                if session_turns:
                    _populate_transcript_windows(ctx.db, session_id, session_turns, cfg)
                log.info("enqueued posthoc jobs session=%s", session_id)
            except Exception as e:
                log.warning("posthoc enqueue failed session=%s: %s", session_id, e)
                posthoc_failed = True
                ctx.add_error("posthoc", ErrorCategory.DEGRADED, str(e), e)

        # Extract job logic does not require DB — always runs
        transcript_path = resolve_transcript_path(payload)
        job_path = _enqueue_extract_job_from_path(transcript_path, payload, cfg)

        fork_spawned = False
        if (
            job_path
            and cfg.extract_fork_cache
            and host == Host.CLAUDE
            and cfg.extract_mode == "async"
        ):
            fork_spawned = _try_fork_extract(session_id, cfg, transcript_path, job_path)

        async_spawned = False
        if job_path and cfg.extract_mode == "async" and not fork_spawned:
            from ..extract.lock import is_locked

            try:
                locked = is_locked(cfg)
            except Exception as e:
                locked = True
                log.warning("is_locked check failed session=%s: %s", session_id, e)
                ctx.add_error("extract_lock", ErrorCategory.DEGRADED, str(e), e)
            if not locked:
                _spawn_async_extract(cfg)
                async_spawned = True
                log.info("spawned async extract after session end")

        ctx.record_event(
            "session_end",
            "ok" if not posthoc_failed else "posthoc_failed",
            details={
                "posthoc_enqueued": posthoc_enqueued,
                "extract_job_written": job_path is not None,
                "fork_extract_spawned": fork_spawned,
                "async_extract_spawned": async_spawned,
            },
        )

    return {"continue": True}


def _enqueue_extract_job_from_path(
    transcript_path: "Path | None", payload: dict, cfg: Config
) -> "Path | None":
    """Write an extract job file for the session's transcript (spec cold-path trigger).

    Returns the job file path if successfully enqueued, None otherwise.
    """
    if transcript_path is None or not transcript_path.exists():
        return None
    try:
        mtime = transcript_path.stat().st_mtime
        project_id = payload.get("project_id") or resolve_project_id(payload.get("cwd") or "")
        job_path = write_extract_job(cfg, transcript_path, project_id, mtime)
        log.info("wrote extract job for %s", transcript_path.name)
        return job_path
    except Exception as e:
        log.warning("extract job write failed: %s", e)
        return None


def _extract_session_turns(payload: dict) -> list[dict]:
    """Extract session turns from the session_end payload for windowing.

    Looks for turns/messages in the payload or conversation field.
    Returns list of dicts with role, content, turn_index, and optional tool fields.
    """
    turns: list[dict] = []

    # Try common payload shapes
    messages = payload.get("messages") or payload.get("conversation") or payload.get("turns")
    if not messages or not isinstance(messages, list):
        return turns

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        turn = {
            "role": msg.get("role", "unknown"),
            "content": msg.get("content", "")
            if isinstance(msg.get("content"), str)
            else str(msg.get("content", "")),
            "turn_index": msg.get("turn_index", i),
        }
        if msg.get("tool_name"):
            turn["tool_name"] = msg["tool_name"]
        if msg.get("tool_input"):
            turn["tool_input"] = str(msg["tool_input"])[:1000]
        turns.append(turn)

    return turns


def _populate_transcript_windows(
    db: Db, session_id: str, session_turns: list[dict], cfg: Config | None = None
) -> None:
    """Store bounded transcript window content for each pending posthoc job.

    Uses windowing module to compute topic-shift-bounded windows per fire event.
    Attempts to use embedding-based topic shift detection if search.embedding is available.
    """
    # Spec section 10: session_end must only enqueue and return immediately.
    # Embedding-based topic shift is deferred to the posthoc background worker.
    embedding_fn = None

    # Get fire events for this session that have pending posthoc jobs
    rows = db.fetchall(
        "SELECT pj.id AS job_id, fe.turn_index, fe.rule_id, r.tool_tags "
        "FROM posthoc_jobs pj "
        "JOIN rule_fire_events fe ON fe.id = pj.fire_event_id "
        "JOIN rules r ON r.id = fe.rule_id "
        "WHERE fe.session_id = ? AND pj.status = 'pending' "
        "AND pj.redacted_window_json IS NULL",
        (session_id,),
    )

    for row in rows:
        turn_index = row["turn_index"]
        if turn_index is None:
            continue

        # Get rule's tool tags for relevance-based windowing
        tool_tags = None
        if row["tool_tags"]:
            try:
                tool_tags = json.loads(row["tool_tags"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Compute bounded window using topic shift detection
        window_turns = compute_event_window(
            session_turns, turn_index, tool_tags, embedding_fn=embedding_fn
        )
        window_content = extract_window_content(window_turns)

        if window_content:
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE posthoc_jobs SET redacted_window_json = ? WHERE id = ?",
                    (window_content, row["job_id"]),
                )


def _try_fork_extract(
    session_id: str,
    cfg: Config,
    transcript_path: "Path | None" = None,
    job_path: "Path | None" = None,
) -> bool:
    """Attempt fork-based extraction. Returns True if successfully spawned."""
    import os
    import subprocess
    import sys

    from ..extract.fork import _claude_cli_available, _valid_session_id

    if not _claude_cli_available():
        log.info("fork extract: claude CLI not available, skipping")
        return False

    if not _valid_session_id(session_id):
        log.warning("fork extract: invalid session_id, skipping")
        return False

    env = os.environ.copy()
    env.pop("NOKORI_EXTRACTING", None)
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)

    cmd = [
        sys.executable,
        "-m",
        "nokori.extract.fork_runner",
        "--session-id",
        session_id,
    ]
    if transcript_path is not None:
        cmd.extend(["--transcript-path", str(transcript_path)])
    if job_path is not None:
        cmd.extend(["--job-path", str(job_path)])

    cfg.ensure_dirs()
    err_log = cfg.logs_dir / "fork-extract.log"
    err_file = None
    try:
        err_file = open(err_log, "a", encoding="utf-8")
    except OSError:
        pass

    try:
        subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_file if err_file is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("fork extract spawned for session=%s", session_id)
        return True
    except Exception as e:
        log.warning("fork extract spawn failed: %s", e)
        return False
    finally:
        if err_file is not None:
            try:
                err_file.close()
            except OSError:
                pass


def _spawn_async_extract(cfg: Config) -> None:
    """Fork a detached subprocess to run `nokori extract`. Best-effort."""
    import os
    import subprocess
    import sys

    _SAFE_VARS = (
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "SHELL",
        "TERM",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
    )
    env = {k: v for k, v in os.environ.items() if k in _SAFE_VARS or k.startswith("NOKORI_")}
    env.pop("NOKORI_EXTRACTING", None)
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)
    cfg.ensure_dirs()
    err_log = cfg.logs_dir / "async-extract.log"
    err_file = None
    try:
        err_file = open(err_log, "a", encoding="utf-8")
    except OSError as e:
        log.warning("async extract log open failed: %s", e)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "nokori", "extract"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_file if err_file is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.warning("async extract spawn failed: %s", e)
    finally:
        if err_file is not None:
            try:
                err_file.close()
            except OSError:
                pass
