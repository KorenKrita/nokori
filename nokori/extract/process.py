"""Shared candidate processing pipeline.

Both the normal extract path (commands/extract.py) and the fork path
(extract/fork_runner.py) produce a list of Candidate objects via different
means. This module provides the single shared function that takes those
candidates and routes them through the cold pipeline, and a higher-level
``extract_transcript`` helper that covers the read→compress→extract→process
flow shared by both callers.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..cold.jobs import enqueue_transcript_ingest, expire_stale_ingest_jobs, mark_ingest_done
from ..cold.pipeline import run_cold_pipeline
from ..cold.roles import PROMPT_VERSIONS
from ..config import Config
from ..db import Db, open_db
from ..extract.compressor import compress
from ..extract.extractor import Candidate, extract as extract_candidates
from ..extract.reader import read_after, read_tail_user_turns
from ..lifecycle.hot_cache import load_last_byte_offset, mark_extracted
from ..llm.adapter import LLMAdapter
from ..utils.logging import get_logger

log = get_logger("nokori.extract.process")

_CONTEXT_TURNS = 3


class LlmUnavailableError(OSError):
    """Raised by extract_transcript when the LLM backend is unreachable."""


def extract_transcript(
    path: Path,
    project_id: str | None,
    cfg: Config,
    db: Db,
    *,
    llm: LLMAdapter | None = None,
    dry_run: bool = False,
) -> tuple[int, int, bool]:
    """Read transcript, compress, extract candidates, and process through cold pipeline.

    The caller is responsible for opening/closing *db*. This function covers the
    read→compress→extract→process flow shared by commands/extract._process_path and
    fork_runner._drain_pending_jobs.

    Args:
        dry_run: If True, stop after extraction (don't process or mark).

    Returns (candidates_found, rules_created, all_ok).
    ``all_ok=True`` with 0 candidates means the transcript was empty after
    compression — the caller should mark it as extracted.

    Raises:
        LlmUnavailableError: When the LLM backend is unreachable.
    """
    prev_offset = load_last_byte_offset(db, path)
    turns, new_offset = read_after(path, prev_offset)

    if prev_offset > 0 and new_offset > prev_offset:
        context = read_tail_user_turns(path, _CONTEXT_TURNS, end_offset=prev_offset)
        turns = context + turns

    text = compress(turns)
    if not text.strip():
        if not dry_run:
            mark_extracted(db, path, _safe_mtime(path), new_offset)
        return (0, 0, True)

    if llm is None:
        llm = LLMAdapter(cfg)
    candidates, llm_ok = extract_candidates(text, llm)
    if not llm_ok:
        raise LlmUnavailableError("LLM backend unreachable during extraction")
    if not candidates:
        if not dry_run:
            mark_extracted(db, path, _safe_mtime(path), new_offset)
        return (0, 0, True)

    if dry_run:
        return (len(candidates), 0, False)

    rules_created, all_ok = process_candidates(
        candidates,
        path,
        project_id,
        cfg,
        transcript_text=text,
        db=db,
    )

    if all_ok:
        mark_extracted(db, path, _safe_mtime(path), new_offset)

    return (len(candidates), rules_created, all_ok)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_EVIDENCE_QUOTE_MAX = 500


def _segment_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _candidate_evidence_quotes(cand: Candidate, transcript_text: str | None) -> list[str]:
    """Return transcript evidence — prefer extractor-returned verbatim quotes."""
    if not transcript_text or not transcript_text.strip():
        return cand.evidence_quotes or []

    haystack = transcript_text.strip()
    if cand.evidence_quotes:
        lower = haystack.lower()
        verified = []
        for q in cand.evidence_quotes:
            if q in haystack:
                verified.append(q[:_EVIDENCE_QUOTE_MAX])
            elif q.lower() in lower:
                idx = lower.find(q.lower())
                verified.append(haystack[idx : idx + len(q)][:_EVIDENCE_QUOTE_MAX])
        if verified:
            return verified

    needles = [
        cand.trigger,
        cand.action,
        cand.behavior,
        cand.rationale,
        *cand.trigger_variants,
    ]
    lower = haystack.lower()
    for needle in needles:
        if not needle:
            continue
        idx = lower.find(str(needle).strip().lower())
        if idx < 0:
            continue
        start = max(0, idx - 160)
        end = min(len(haystack), idx + len(str(needle)) + 240)
        return [haystack[start:end].strip()[:_EVIDENCE_QUOTE_MAX]]
    return [haystack[:_EVIDENCE_QUOTE_MAX]]


def process_candidates(
    candidates: list[Candidate],
    transcript_path: Path,
    project_id: str | None,
    cfg: Config,
    *,
    transcript_text: str | None = None,
    db: Db | None = None,
    llm: LLMAdapter | None = None,
) -> tuple[int, bool]:
    """Route extracted candidates through the cold pipeline.

    Args:
        candidates: Parsed extraction candidates.
        transcript_path: Path to the transcript (used for seg_hash and transcript_ref).
        project_id: Optional project scope.
        cfg: Config instance.
        transcript_text: If available, used to verify evidence_quotes against
            actual transcript content. Fork path does not have this.
        db: Optional database connection. If provided, caller owns the lifecycle;
            otherwise a new connection is opened and closed internally.
        llm: Optional LLM adapter. If provided, reused; otherwise created internally.

    Returns:
        (rules_created, all_ok)
    """
    owns_db = db is None
    if owns_db:
        db = open_db(cfg.db_path)
    if db is None:
        raise RuntimeError("db must not be None after open_db")
    try:
        if llm is None:
            llm = LLMAdapter(cfg)
        rules_created = 0
        all_ok = True
        transcript_ref = str(transcript_path)
        try:
            expire_stale_ingest_jobs(db)
        except Exception as exc:
            log.debug("expire_stale_ingest_jobs failed (non-fatal): %s", exc)

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
                "evidence_quotes": _candidate_evidence_quotes(cand, transcript_text),
            }

            segment_text = (
                f"{transcript_ref}::{extractor_output['trigger']}::{extractor_output['action']}"
            )
            seg_hash = _segment_hash(segment_text)

            try:
                existing_job = db.fetchone(
                    "SELECT id FROM transcript_ingest_jobs "
                    "WHERE segment_hash = ? AND extractor_prompt_version = ? "
                    "AND status = 'done'",
                    (seg_hash, PROMPT_VERSIONS["extractor"]),
                )
                if existing_job:
                    continue
            except Exception as exc:
                log.warning("dedup check failed segment=%s: %s", seg_hash[:8], exc)
                all_ok = False
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
                    llm,
                    transcript_ref=transcript_ref,
                    extractor_output=extractor_output,
                    role_models=cfg.role_models,
                    default_model=cfg.llm_model,
                    project_id=project_id,
                    role_max_tokens=cfg.role_max_tokens,
                    role_timeouts=cfg.role_timeouts,
                )
                if not result.status.startswith("pending"):
                    mark_ingest_done(db, seg_hash, PROMPT_VERSIONS["extractor"])
                if result.rule_id is not None:
                    rules_created += 1
            except Exception as exc:
                log.warning(
                    "cold pipeline failed for candidate: %s (%s)", (cand.trigger or "")[:60], exc
                )
                all_ok = False
    finally:
        if owns_db:
            db.close()

    return (rules_created, all_ok)
