"""Background runner for fork-based extraction.

Spawned as a detached subprocess by session_end hook. Forks the Claude Code
session to get extraction JSON (reusing prompt cache), then feeds it through
the same shared pipeline as the normal extract path.

Offset-aware: reads the transcript to find the last extracted byte offset,
then provides an anchor user message so the forked model only extracts from
new content. Skips fork if compression occurred after the offset.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import Config
from ..db import open_db
from ..events.observability import write_event
from ..extract.extractor import _parse_candidates
from ..extract.fork import fork_extract
from ..extract.jobs import delete_job, find_project_id_for_transcript
from ..extract.lock import acquire as extract_lock
from ..extract.process import process_candidates
from ..lifecycle.hot_cache import load_last_byte_offset, mark_extracted
from ..llm.prompts import EXTRACT_SYSTEM, UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from ..utils.logging import get_logger

log = get_logger("nokori.extract.fork_runner")


def _build_extraction_prompt(anchor_text: str | None) -> str:
    """Build the extraction prompt, optionally with an anchor for incremental extraction."""
    base = EXTRACT_SYSTEM

    if anchor_text:
        safe_anchor = anchor_text[:500].replace(UNTRUSTED_CLOSE, "[REDACTED]")
        base += (
            "\n\n=== INCREMENTAL EXTRACTION ===\n"
            "This conversation has been partially extracted before. "
            "Focus on conversation starting from around this user message:\n"
            f"{UNTRUSTED_OPEN}\n"
            f"{safe_anchor}\n"
            f"{UNTRUSTED_CLOSE}\n"
            "Content BEFORE that message has already been extracted. "
            "You may use it as context to understand ongoing exchanges, "
            "but only extract NEW corrections/preferences from that message onward.\n"
        )

    base += (
        "\n\nThe conversation above is the transcript to extract from.\n"
        f"{UNTRUSTED_OPEN}\n(transcript is the conversation history above)\n{UNTRUSTED_CLOSE}"
    )
    return base


def _has_compact_after_offset(transcript_path: Path, byte_offset: int) -> bool:
    """Check if a compact_boundary event exists after the given byte offset."""
    if byte_offset <= 0:
        return False
    if not transcript_path.exists():
        return False
    try:
        with open(transcript_path, "rb") as f:
            f.seek(byte_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if b"compact_boundary" in line:
                    try:
                        obj = json.loads(line)
                        if obj.get("subtype") == "compact_boundary":
                            return True
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
    except OSError:
        return False
    return False


_CONTEXT_TURNS_BACK = 3


def _read_anchor_user_message(transcript_path: Path, byte_offset: int) -> str | None:
    """Read the Nth user message before byte_offset as an anchor point.

    Goes back _CONTEXT_TURNS_BACK user messages so the model has overlap context
    for corrections that span the extraction boundary.
    """
    if byte_offset <= 0 or not transcript_path.exists():
        return None
    block_size = 32768
    start = max(0, byte_offset - block_size)
    try:
        with open(transcript_path, "rb") as f:
            f.seek(start)
            chunk = f.read(byte_offset - start)
    except OSError:
        return None

    user_messages: list[str] = []
    lines = chunk.split(b"\n")
    for raw_line in reversed(lines):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        if b'"user"' not in raw_line and b'"human"' not in raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if obj.get("type") not in ("user", "human"):
            continue
        msg = obj.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", "")
        else:
            content = obj.get("content", "")
        text = _extract_text_content(content)
        if text and len(text) > 10:
            user_messages.append(text)
            if len(user_messages) >= _CONTEXT_TURNS_BACK:
                break

    if not user_messages:
        return None
    return user_messages[-1]


def _extract_text_content(content) -> str:
    """Extract plain text from message content (string or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return ""


def _mark_extracted_safe(cfg: Config, t_path: Path) -> None:
    """Advance the byte offset to current file size."""
    try:
        st = t_path.stat()
        db = open_db(cfg.db_path)
        try:
            mark_extracted(db, t_path, st.st_mtime, st.st_size)
        finally:
            db.close()
    except Exception as exc:
        log.warning("mark_extracted failed: %s", exc)


def _write_event_safe(cfg: Config, session_id: str, outcome: str, details: dict) -> None:
    try:
        db = open_db(cfg.db_path)
        try:
            write_event(
                db, source="fork_extract", session_id=session_id, outcome=outcome, details=details
            )
        finally:
            db.close()
    except Exception as exc:
        log.debug("write_event_safe failed: %s", exc)


def _drain_pending_jobs(cfg: Config, *, exclude_job: Path | None = None) -> None:
    """Process any remaining pending extract jobs while we already hold the lock.

    Called after the primary fork extraction completes (success or failure) to
    pick up jobs that were deferred by other fork_runners that found the lock busy.
    """
    from ..extract import jobs as job_io
    from ..extract.process import LlmUnavailableError, extract_transcript

    pending = job_io.list_jobs(cfg)
    if not pending:
        return

    other_jobs = [p for p in pending if p != exclude_job]
    if not other_jobs:
        return

    log.info("draining %d pending extract jobs", len(other_jobs))
    consecutive_failures = 0
    db = open_db(cfg.db_path)
    try:
        for jp in other_jobs:
            if consecutive_failures >= 3:
                log.warning("drain: %d consecutive failures, stopping early", consecutive_failures)
                break

            job = job_io.read_job(jp)
            if not job:
                job_io.quarantine_corrupt_job(jp, cfg)
                continue
            t_path = Path(job["transcript_path"])
            if not t_path.exists():
                delete_job(jp)
                continue
            try:
                project_id = find_project_id_for_transcript(cfg, t_path)
                cands, rules_created, all_ok = extract_transcript(
                    t_path, project_id, cfg, db
                )
                if all_ok:
                    delete_job(jp)
                consecutive_failures = 0
                log.info(
                    "drain job done: %s candidates=%d rules=%d",
                    jp.name,
                    cands,
                    rules_created,
                )
            except LlmUnavailableError:
                log.warning("drain: LLM unavailable, stopping drain early")
                return
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("drain job corrupt, quarantining: %s %s", jp.name, exc)
                job_io.quarantine_corrupt_job(jp, cfg)
            except Exception as exc:
                consecutive_failures += 1
                log.warning("drain job failed (will retry): %s %s", jp.name, exc)
    finally:
        db.close()


def run(session_id: str, transcript_path: str | None = None, job_path: str | None = None) -> int:
    cfg = Config.from_env()
    if cfg.disabled:
        return 0

    from ..utils.logging import configure

    configure(cfg.logs_dir, level=cfg.log_level)

    with extract_lock(cfg) as locked:
        if not locked:
            log.info("extract lock held, fork extract deferred")
            return 2

        t_path = Path(transcript_path) if transcript_path else None
        j_path = Path(job_path) if job_path else None

        # --- Pre-extraction: offset & compression check ---
        anchor_text: str | None = None
        byte_offset = 0

        if t_path and t_path.exists():
            db = open_db(cfg.db_path)
            try:
                byte_offset = load_last_byte_offset(db, t_path)
            finally:
                db.close()

            if byte_offset > 0:
                if _has_compact_after_offset(t_path, byte_offset):
                    log.info(
                        "compression detected after offset, skipping fork for session=%s",
                        session_id,
                    )
                    _write_event_safe(
                        cfg,
                        session_id,
                        "fork_extract_skipped_compressed",
                        {
                            "byte_offset": byte_offset,
                        },
                    )
                    _drain_pending_jobs(cfg, exclude_job=j_path)
                    return 1

                anchor_text = _read_anchor_user_message(t_path, byte_offset)
                if anchor_text:
                    log.info(
                        "incremental fork extract from offset=%d anchor=%s...",
                        byte_offset,
                        anchor_text[:40],
                    )

        # --- Get candidates via fork (the ONLY difference from normal path) ---
        prompt = _build_extraction_prompt(anchor_text)
        raw = fork_extract(session_id, prompt, cfg)
        if raw is None:
            log.warning("fork extract failed for session=%s", session_id)
            _write_event_safe(cfg, session_id, "fork_extract_failed", {})
            _drain_pending_jobs(cfg, exclude_job=j_path)
            return 1

        candidates, parse_ok = _parse_candidates(raw)
        if not parse_ok:
            log.warning("fork extract parse failed for session=%s", session_id)
            _write_event_safe(
                cfg, session_id, "fork_extract_parse_failed", {"raw_preview": raw[:200]}
            )
            _drain_pending_jobs(cfg, exclude_job=j_path)
            return 1

        if not candidates:
            log.info("fork extract found 0 candidates for session=%s", session_id)
            _write_event_safe(cfg, session_id, "fork_extract_empty", {})
            if t_path and t_path.exists():
                _mark_extracted_safe(cfg, t_path)
            if j_path:
                delete_job(j_path)
            _drain_pending_jobs(cfg, exclude_job=j_path)
            return 0

        # --- Process candidates through shared pipeline ---
        project_id = find_project_id_for_transcript(cfg, t_path) if t_path else None

        try:
            rules_created, all_ok = process_candidates(
                candidates,
                t_path or Path(f"fork:{session_id}"),
                project_id,
                cfg,
            )
        except Exception as exc:
            log.error("process_candidates crashed: %s", exc)
            _write_event_safe(cfg, session_id, "fork_extract_pipeline_error", {"error": str(exc)})
            _drain_pending_jobs(cfg, exclude_job=j_path)
            return 1

        # --- Post-extraction: mark offset & cleanup job ---
        if all_ok:
            if t_path and t_path.exists():
                _mark_extracted_safe(cfg, t_path)
            if j_path:
                delete_job(j_path)
        else:
            log.warning("fork extract partial, offset not advanced: session=%s", session_id)

        _write_event_safe(
            cfg,
            session_id,
            "fork_extract_ok",
            {
                "candidates": len(candidates),
                "rules_created": rules_created,
                "all_ok": all_ok,
                "incremental": anchor_text is not None,
            },
        )
        log.info(
            "fork extract complete: session=%s candidates=%d rules=%d all_ok=%s",
            session_id,
            len(candidates),
            rules_created,
            all_ok,
        )

        _drain_pending_jobs(cfg, exclude_job=j_path)
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--transcript-path", default=None)
    parser.add_argument("--job-path", default=None)
    args = parser.parse_args()
    sys.exit(run(args.session_id, args.transcript_path, args.job_path))


if __name__ == "__main__":
    main()
