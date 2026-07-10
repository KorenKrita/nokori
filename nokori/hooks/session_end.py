from __future__ import annotations

import contextlib
import json
from pathlib import Path

from ..config import Config
from ..db import Db
from ..extract.jobs import write_job as write_extract_job
from ..gate import prompt_ack
from ..gate.marker import prompt_hash
from ..posthoc import enqueue_posthoc_for_session
from ..posthoc.windowing import compute_event_window, extract_window_content
from ..utils import sessions
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id
from ..utils.prompt_text import normalize_prompt_for_hash
from ..utils.transcript import resolve_transcript_path
from .context import ErrorCategory, HotPathContext

log = get_logger("nokori.hooks.session_end")

# Environment variables passed through to extract subprocesses. Kept consistent
# across _try_fork_extract and _spawn_async_extract so both inherit proxy/cert
# config needed by the claude CLI in corporate networks.
_EXTRACT_SAFE_VARS = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "SHELL",
    "TERM",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
)
_EXTRACT_SAFE_PREFIXES = ("NOKORI_", "ANTHROPIC_", "CLAUDE_")


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
                async_spawned = bool(_spawn_async_extract(cfg))
                if async_spawned:
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
    transcript_path: Path | None, payload: dict, cfg: Config
) -> Path | None:
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

    When a fire event has turn_index=None (Claude Code / Cursor UserPromptSubmit
    payloads don't carry turn_index), the injection turn is located by matching
    the fire event's prompt_hash against user turns in session_turns. Previously
    such events were skipped, leaving redacted_window_json NULL and forcing
    posthoc to fall back to the (often polluted) bounded_window_ref prompt text.
    """
    # Spec section 10: session_end must only enqueue and return immediately.
    # Embedding-based topic shift is deferred to the posthoc background worker.
    embedding_fn = None

    # Index user turns by their prompt_hash for O(1) lookup when turn_index is
    # missing. Only user turns can be injection points (rules fire on user
    # prompt submit).
    user_turns_by_hash: dict[str, int] = {}
    for idx, turn in enumerate(session_turns):
        role = turn.get("role")
        # Normalize "human" → "user" in place so compute_event_window's
        # topic-shift / stop conditions (which check role == "user") behave
        # consistently with the jobs.py re-derivation path.
        if role == "human":
            turn = {**turn, "role": "user"}
            session_turns[idx] = turn
            role = "user"
        if role != "user":
            continue
        content = turn.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        normalized = normalize_prompt_for_hash(content)
        # Hash caliber mirrors prompt_inject.py: prompt_hash(normalized or content).
        # content is guaranteed non-empty by the guard above.
        ph = prompt_hash(normalized or content)
        # Store the turn's own turn_index (compute_event_window looks up by
        # turn.get("turn_index") == injection_turn_index). Coerce string values
        # (from hook payloads) to int and write back so the lookup matches;
        # fall back to the list position when no usable value is available.
        raw_turn_idx = turn.get("turn_index", idx)
        try:
            turn_idx = int(raw_turn_idx)
        except (TypeError, ValueError):
            turn_idx = idx
        if turn.get("turn_index") != turn_idx:
            turn = {**turn, "turn_index": turn_idx}
            session_turns[idx] = turn
        # First match wins; later duplicate prompts in the same session are
        # unlikely and using the earliest keeps the window anchored to the
        # original injection.
        user_turns_by_hash.setdefault(ph, turn_idx)

    # Get fire events for this session that have pending posthoc jobs
    rows = db.fetchall(
        "SELECT pj.id AS job_id, fe.turn_index, fe.prompt_hash, fe.rule_id, r.tool_tags "
        "FROM posthoc_jobs pj "
        "JOIN rule_fire_events fe ON fe.id = pj.fire_event_id "
        "JOIN rules r ON r.id = fe.rule_id "
        "WHERE fe.session_id = ? AND pj.status = 'pending' "
        "AND pj.redacted_window_json IS NULL",
        (session_id,),
    )

    for row in rows:
        turn_index = row["turn_index"]
        ph = row["prompt_hash"]
        # Validate turn_index whenever present: coerce to int, find the
        # candidate by field value (turn_index is a field, not a list
        # position), require it to be a user turn, and (when prompt_hash is
        # available) cross-check the hash. A stale/mis-offset turn_index must
        # not anchor the window on the wrong turn — fall back to hash lookup.
        if turn_index is not None:
            try:
                ti = int(turn_index)
            except (TypeError, ValueError):
                ti = None
            cand = (
                next((t for t in session_turns if t.get("turn_index") == ti), None)
                if ti is not None
                else None
            )
            if cand is not None and cand.get("role") == "user" and isinstance(
                cand.get("content"), str
            ):
                if ph:
                    cand_ph = prompt_hash(
                        normalize_prompt_for_hash(cand["content"]) or cand["content"]
                    )
                    # validated — pass the int field value; mismatch falls through to hash lookup
                    turn_index = ti if cand_ph == ph else None
                else:
                    # No hash to cross-check, but still require a valid user
                    # turn and pass the coerced int field value.
                    turn_index = ti
            else:
                turn_index = None
        if turn_index is None:
            # Locate injection turn by prompt_hash against user turns.
            if not ph:
                continue
            turn_index = user_turns_by_hash.get(ph)
            if turn_index is None:
                # session_end payload may not carry the full transcript
                # (Cursor/Claude messages field is unstable); the posthoc
                # background worker will re-derive from the transcript file
                # via _load_transcript_window.
                continue

        # Get rule's tool tags for relevance-based windowing
        tool_tags = None
        if row["tool_tags"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                tool_tags = json.loads(row["tool_tags"])

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
    transcript_path: Path | None = None,
    job_path: Path | None = None,
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

    env = {
        k: v
        for k, v in os.environ.items()
        if k in _EXTRACT_SAFE_VARS
        or any(k.startswith(prefix) for prefix in _EXTRACT_SAFE_PREFIXES)
    }
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
    with contextlib.suppress(OSError):
        err_file = open(err_log, "a", encoding="utf-8")

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
            with contextlib.suppress(OSError):
                err_file.close()


def _spawn_async_extract(cfg: Config) -> bool:
    """Fork a detached `nokori extract` subprocess. Return whether spawn succeeded."""
    import os
    import subprocess
    import sys

    env = {
        k: v
        for k, v in os.environ.items()
        if k in _EXTRACT_SAFE_VARS
        or any(k.startswith(prefix) for prefix in _EXTRACT_SAFE_PREFIXES)
    }
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
        return True
    except Exception as e:
        log.warning("async extract spawn failed: %s", e)
        return False
    finally:
        if err_file is not None:
            with contextlib.suppress(OSError):
                err_file.close()
