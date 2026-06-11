"""Background runner for fork-based extraction.

Spawned as a detached subprocess by session_end hook. Forks the Claude Code
session, parses the extraction output, and feeds it through the cold pipeline.

Offset-aware: reads the transcript to find the last extracted byte offset,
then provides an anchor user message so the forked model only extracts from
new content. Skips fork if compression occurred after the offset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from ..cold.jobs import enqueue_transcript_ingest
from ..cold.pipeline import run_cold_pipeline
from ..cold.roles import PROMPT_VERSIONS
from ..config import Config
from ..db import open_db
from ..events.observability import write_event
from ..extract.extractor import _parse_candidates
from ..extract.fork import fork_extract
from ..extract.lock import acquire as extract_lock
from ..lifecycle.hot_cache import load_last_byte_offset, mark_extracted
from ..llm.adapter import LLMAdapter
from ..llm.prompts import EXTRACT_SYSTEM, UNTRUSTED_OPEN, UNTRUSTED_CLOSE
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
    # Return the earliest (Nth back) message as the anchor
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


class _ColdLLMAdapter:
    """Adapts LLMAdapter to the cold pipeline's llm.call() interface."""

    def __init__(self, llm: LLMAdapter):
        self._llm = llm

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 2000,
        timeout: int = 30,
    ) -> str:
        if self._llm.configured():
            result = self._llm._call_openai_compatible(
                system, user, max_tokens, timeout, model_id=model
            )
        else:
            result = self._llm._fallback_claude_cli(system, user, timeout)
        if result is None:
            raise RuntimeError("LLM call returned None")
        return result


def run(session_id: str, transcript_path: str | None = None) -> int:
    cfg = Config.from_env()
    if cfg.disabled:
        return 0

    from ..utils.logging import configure
    configure(cfg.logs_dir, level=cfg.log_level)

    with extract_lock(cfg) as locked:
        if not locked:
            log.info("extract lock held, fork extract deferred")
            return 2

        # Resolve transcript path and check offset/compression
        t_path = Path(transcript_path) if transcript_path else None
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
                    log.info("compression detected after offset, skipping fork for session=%s", session_id)
                    write_event_safe(cfg, session_id, "fork_extract_skipped_compressed", {
                        "byte_offset": byte_offset,
                    })
                    return 1

                anchor_text = _read_anchor_user_message(t_path, byte_offset)
                if anchor_text:
                    log.info("incremental fork extract from offset=%d anchor=%s...",
                             byte_offset, anchor_text[:40])

        prompt = _build_extraction_prompt(anchor_text)
        raw = fork_extract(session_id, prompt, cfg)
        if raw is None:
            log.warning("fork extract failed for session=%s", session_id)
            write_event_safe(cfg, session_id, "fork_extract_failed", {})
            return 1

        candidates, parse_ok = _parse_candidates(raw)
        if not parse_ok:
            log.warning("fork extract parse failed for session=%s", session_id)
            write_event_safe(cfg, session_id, "fork_extract_parse_failed", {"raw_preview": raw[:200]})
            return 1

        if not candidates:
            log.info("fork extract found 0 candidates for session=%s", session_id)
            write_event_safe(cfg, session_id, "fork_extract_empty", {})
            # Mark extracted even with 0 candidates so offset advances
            if t_path and t_path.exists():
                _mark_extracted_safe(cfg, t_path)
            return 0

        db = open_db(cfg.db_path)
        llm = LLMAdapter(cfg)
        cold_llm = _ColdLLMAdapter(llm)
        rules_created = 0
        all_ok = True

        try:
            for cand in candidates:
                extractor_output = {
                    "trigger": cand.trigger or "",
                    "trigger_zh": cand.trigger_text_zh or "",
                    "trigger_variants": cand.trigger_variants or [],
                    "trigger_variants_zh": cand.trigger_variants_zh or [],
                    "search_terms": cand.search_terms or {},
                    "required_concepts": cand.required_concepts or [],
                    "excluded_contexts": cand.excluded_contexts or [],
                    "non_generalization_boundaries": cand.non_generalization_boundaries or [],
                    "near_miss_examples": cand.near_miss_examples or [],
                    "severity": cand.severity or "reminder",
                    "domain_tags": cand.domain_tags or [],
                    "tool_tags": cand.tool_tags or [],
                    "file_or_path_patterns": cand.file_or_path_patterns or [],
                    "behavior": cand.behavior or "",
                    "action": cand.action or "",
                    "action_zh": cand.action_zh or "",
                    "evidence_quotes": cand.evidence_quotes or [],
                }

                segment_text = json.dumps(
                    [session_id, extractor_output["trigger"], extractor_output["action"], extractor_output["behavior"]],
                    separators=(",", ":"), ensure_ascii=False,
                )
                seg_hash = hashlib.sha256(segment_text.encode("utf-8")).hexdigest()[:16]
                transcript_ref = f"fork:{session_id}"

                existing_job = db.fetchone(
                    "SELECT id FROM transcript_ingest_jobs "
                    "WHERE segment_hash = ? AND extractor_prompt_version = ? "
                    "AND status IN ('pending', 'done')",
                    (seg_hash, PROMPT_VERSIONS["extractor"]),
                )
                if existing_job:
                    continue

                try:
                    enqueue_transcript_ingest(
                        db,
                        transcript_ref=transcript_ref,
                        segment_hash=seg_hash,
                        extractor_prompt_version=PROMPT_VERSIONS["extractor"],
                    )
                except Exception as exc:
                    log.warning("enqueue_transcript_ingest failed: %s", exc)
                    all_ok = False
                    continue

                try:
                    result = run_cold_pipeline(
                        db,
                        cold_llm,
                        transcript_ref=transcript_ref,
                        extractor_output=extractor_output,
                        role_models=cfg.role_models,
                        default_model=cfg.llm_model,
                        role_max_tokens=cfg.role_max_tokens,
                        role_timeouts=cfg.role_timeouts,
                    )
                    if result.rule_id is not None:
                        rules_created += 1
                except Exception as exc:
                    log.warning("cold pipeline failed: %s", exc)
                    all_ok = False
        finally:
            db.close()

        if all_ok and t_path and t_path.exists():
            _mark_extracted_safe(cfg, t_path)
        elif not all_ok:
            log.warning("fork extract incomplete, offset not advanced for session=%s", session_id)

        write_event_safe(cfg, session_id, "fork_extract_ok", {
            "candidates": len(candidates),
            "rules_created": rules_created,
            "all_ok": all_ok,
            "incremental": anchor_text is not None,
        })
        log.info("fork extract complete: session=%s candidates=%d rules=%d all_ok=%s",
                 session_id, len(candidates), rules_created, all_ok)
        return 0


def _mark_extracted_safe(cfg: Config, t_path: Path) -> None:
    """Advance the byte offset to current file size."""
    try:
        st = t_path.stat()
        size = st.st_size
        mtime = st.st_mtime
        db = open_db(cfg.db_path)
        try:
            mark_extracted(db, t_path, mtime, size)
        finally:
            db.close()
    except Exception as exc:
        log.warning("mark_extracted failed: %s", exc)


def write_event_safe(cfg: Config, session_id: str, outcome: str, details: dict) -> None:
    try:
        db = open_db(cfg.db_path)
        try:
            write_event(db, source="fork_extract", session_id=session_id,
                        outcome=outcome, details=details)
        finally:
            db.close()
    except Exception as exc:
        log.debug("write_event_safe failed: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--transcript-path", default=None)
    args = parser.parse_args()
    sys.exit(run(args.session_id, args.transcript_path))


if __name__ == "__main__":
    main()
