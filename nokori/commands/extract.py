from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..config import Config
from ..constants import TRANSCRIPT_MTIME_EPSILON_SEC
from ..db import open_db
from ..extract import jobs as job_io
from ..extract.lock import acquire as extract_lock
from ..extract.compressor import compress
from ..extract.extractor import extract as extract_candidates
from ..extract import checkpoint as merge_checkpoint
from ..extract.merger import merge_candidate
from ..extract.reader import read as read_transcript
from ..lifecycle.hot_cache import mark_extracted
from ..llm.adapter import LLMAdapter
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id

log = get_logger("nokori.commands.extract")


def _process_path(path: Path, project_id: str | None, cfg: Config,
                  *, dry_run: bool) -> tuple[int, int, bool]:
    turns = read_transcript(path)
    text = compress(turns)
    if not text.strip():
        if not dry_run:
            db = open_db(cfg.db_path)
            try:
                mark_extracted(db, path, path.stat().st_mtime)
            finally:
                db.close()
        return (0, 0, True)
    llm = LLMAdapter(cfg)
    candidates, llm_ok = extract_candidates(text, llm)
    if not llm_ok:
        log.warning("extract failed (llm): %s", path)
        return (0, 0, False)
    if dry_run:
        return (len(candidates), 0, False)

    db = open_db(cfg.db_path)
    try:
        merged = 0
        merge_ok = True
        done_keys = merge_checkpoint.load_merged_keys(cfg, path)
        for cand in candidates:
            if merge_checkpoint.candidate_keys(cand) & done_keys:
                continue
            outcome = merge_candidate(cand, db, llm, project_id, cfg=cfg)
            if not outcome.merge_ok:
                merge_ok = False
                log.warning(
                    "extract merge stopped at candidate, checkpoint preserved: %s",
                    path,
                )
                break
            merge_checkpoint.record_candidate_merged(cfg, path, cand, done_keys)
            done_keys |= merge_checkpoint.candidate_keys(cand)
            merged += outcome.inserted + outcome.activated + outcome.superseded
        if merge_ok:
            try:
                final_mtime = path.stat().st_mtime
            except OSError:
                final_mtime = 0.0
            mark_extracted(db, path, final_mtime)
            merge_checkpoint.clear(cfg, path)
        else:
            log.warning("extract merge incomplete, transcript not marked extracted: %s", path)
    finally:
        db.close()
    return (len(candidates), merged, merge_ok)


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
