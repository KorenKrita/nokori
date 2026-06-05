from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from ..cold.jobs import enqueue_transcript_ingest, expire_stale_ingest_jobs
from ..cold.pipeline import run_cold_pipeline
from ..cold.roles import PROMPT_VERSIONS
from ..config import Config
from ..constants import TRANSCRIPT_MTIME_EPSILON_SEC
from ..db import open_db
from ..extract import jobs as job_io
from ..extract.lock import acquire as extract_lock
from ..extract.compressor import compress
from ..extract.extractor import extract as extract_candidates
from ..extract.reader import read_after, read_tail_user_turns
from ..lifecycle.hot_cache import load_last_byte_offset, mark_extracted
from ..llm.adapter import LLMAdapter
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id

log = get_logger("nokori.commands.extract")


_CONTEXT_TURNS = 2
_EVIDENCE_QUOTE_MAX = 500


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
        configured = getattr(self._llm, "configured", None)
        if callable(configured) and configured():
            result = self._llm._call_openai_compatible(
                system, user, max_tokens, timeout, model_id=model
            )
        elif hasattr(self._llm, "complete_messages"):
            result = self._llm.complete_messages(
                system, user, max_tokens=max_tokens, timeout=timeout
            )
        else:
            result = self._llm._fallback_claude_cli(system, user, timeout)
        if result is None:
            raise RuntimeError("LLM call returned None")
        return result


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _segment_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _candidate_evidence_quotes(cand, transcript_text: str) -> list[str]:
    """Return transcript evidence — prefer extractor-returned verbatim quotes."""
    haystack = transcript_text.strip()
    if not haystack:
        return []
    # Prefer quotes returned by the extractor (verbatim from transcript)
    if cand.evidence_quotes:
        verified = []
        for q in cand.evidence_quotes:
            if q in haystack:
                verified.append(q[:_EVIDENCE_QUOTE_MAX])
            elif q.lower() in haystack.lower():
                idx = haystack.lower().find(q.lower())
                verified.append(haystack[idx:idx + len(q)][:_EVIDENCE_QUOTE_MAX])
        if verified:
            return verified
    # Fallback: search for needles from candidate fields
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


def _process_path(path: Path, project_id: str | None, cfg: Config,
                  *, dry_run: bool) -> tuple[int, int, bool]:
    """Read transcript, extract candidates, enqueue through cold pipeline.

    Returns (candidates_found, rules_created, finished).
    """
    db = open_db(cfg.db_path)
    try:
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

        llm = LLMAdapter(cfg)
        candidates, llm_ok = extract_candidates(text, llm)
        if not llm_ok:
            log.warning("extract failed (llm): %s", path)
            return (0, 0, False)
        if dry_run:
            return (len(candidates), 0, False)

        # Route each candidate through the cold pipeline
        cold_llm = _ColdLLMAdapter(llm)
        rules_created = 0
        all_ok = True

        for cand in candidates:
            # Build extractor_output dict matching cold pipeline's expected format
            extractor_output = {
                "trigger": cand.trigger,
                "trigger_draft": cand.trigger,
                "trigger_zh": cand.trigger_text_zh,
                "action": cand.action,
                "action_draft": cand.action,
                "action_zh": cand.action_zh,
                "evidence_quotes": _candidate_evidence_quotes(cand, text),
                "trigger_variants_draft": cand.trigger_variants,
                "trigger_variants_zh": cand.trigger_variants_zh,
                "search_terms_draft": cand.search_terms,
                "domain_tags": [],
                "tool_tags": [],
                "path_patterns": [],
            }

            # Enqueue transcript ingest job for auditability
            segment_text = f"{cand.trigger}::{cand.action}"
            seg_hash = _segment_hash(segment_text)
            transcript_ref = str(path)

            enqueue_transcript_ingest(
                db,
                transcript_ref=transcript_ref,
                segment_hash=seg_hash,
                extractor_prompt_version=PROMPT_VERSIONS["extractor"],
            )

            # Run through cold pipeline
            try:
                result = run_cold_pipeline(
                    db,
                    cold_llm,
                    transcript_ref=transcript_ref,
                    extractor_output=extractor_output,
                    role_models=cfg.role_models,
                    default_model=cfg.llm_model,
                    project_id=project_id,
                    role_max_tokens=cfg.role_max_tokens,
                    role_timeouts=cfg.role_timeouts,
                )
                if result.rule_id is not None:
                    rules_created += 1
            except Exception as exc:
                log.warning("cold pipeline failed for candidate: %s (%s)", cand.trigger[:60], exc)
                all_ok = False

        if all_ok:
            mark_extracted(db, path, _safe_mtime(path), new_offset)
        else:
            log.warning("extract incomplete (cold pipeline errors), transcript not marked: %s", path)
    finally:
        db.close()
    return (len(candidates), rules_created, all_ok)


def run(args: argparse.Namespace, cfg: Config) -> int:
    if cfg.disabled:
        print("nokori: disabled (NOKORI_DISABLED)")
        return 0
    with extract_lock(cfg) as locked:
        if not locked:
            print("(extract already running)")
            return 2

        if args.session:
            path = Path(args.session).expanduser().resolve()
            if not path.exists():
                print(f"nokori: transcript not found: {path}")
                return 1
            if getattr(args, "project", None):
                project_id = args.project
            else:
                project_id = job_io.find_project_id_for_transcript(cfg, path)
                if project_id is None:
                    project_id = resolve_project_id(os.getcwd())
            cands, applied, _finished = _process_path(
                path, project_id, cfg, dry_run=args.dry_run
            )
            print(f"transcript: {path}")
            print(f"candidates: {cands}")
            if not args.dry_run:
                print(f"applied:    {applied}")
            return 0

        # Expire stale ingest jobs before processing
        db = open_db(cfg.db_path)
        try:
            expire_stale_ingest_jobs(db)
        finally:
            db.close()

        pending = job_io.list_jobs(cfg)
        if not pending:
            print("(no pending extract jobs)")
            return 0

        total_cands = 0
        total_applied = 0
        for job_path in pending:
            job = job_io.read_job(job_path)
            if not job:
                job_io.quarantine_corrupt_job(job_path, cfg)
                log.warning("quarantined corrupt extract job: %s", job_path.name)
                continue
            path = Path(job["transcript_path"])
            if not path.exists():
                job_io.delete_job(job_path)
                continue
            job_mtime = job.get("transcript_mtime")
            current_mtime = path.stat().st_mtime
            if (
                job_mtime is not None
                and abs(float(job_mtime) - float(current_mtime))
                > TRANSCRIPT_MTIME_EPSILON_SEC
            ):
                job_path = job_io.refresh_job_mtime(
                    cfg, job_path, path, job.get("project_id"), current_mtime,
                )
                job = job_io.read_job(job_path)
                if not job:
                    continue
            cands, applied, finished = _process_path(
                path, job.get("project_id"), cfg, dry_run=args.dry_run
            )
            total_cands += cands
            total_applied += applied
            if not args.dry_run:
                if finished:
                    job_io.delete_job(job_path)
                else:
                    log.warning(
                        "extract job kept pending (not finished): %s", job_path.name
                    )

        print(f"jobs:       {len(pending)}")
        print(f"candidates: {total_cands}")
        if not args.dry_run:
            print(f"applied:    {total_applied}")
        return 0
