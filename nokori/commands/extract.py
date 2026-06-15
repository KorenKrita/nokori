from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..cold.jobs import expire_stale_ingest_jobs
from ..config import Config
from ..constants import TRANSCRIPT_MTIME_EPSILON_SEC
from ..db import open_db
from ..events.observability import write_event
from ..extract import jobs as job_io
from ..extract.lock import acquire as extract_lock
from ..extract.process import LlmUnavailableError, extract_transcript
from ..llm.adapter import LLMAdapter
from ..utils.logging import get_logger
from ..utils.project import resolve_project_id

log = get_logger("nokori.commands.extract")


def _process_path(
    path: Path, project_id: str | None, cfg: Config, *, dry_run: bool
) -> tuple[int, int, bool]:
    """Read transcript, extract candidates, enqueue through cold pipeline.

    Returns (candidates_found, rules_created, finished).
    """
    db = open_db(cfg.db_path)
    try:
        llm = LLMAdapter(cfg)
        try:
            cands, rules_created, all_ok = extract_transcript(
                path, project_id, cfg, db, llm=llm, dry_run=dry_run
            )
        except LlmUnavailableError:
            log.warning("extract failed (llm): %s", path)
            if not dry_run:
                write_event(
                    db,
                    source="cli_extract",
                    outcome="llm_failure",
                    details={"transcript": path.name, "project_id": project_id},
                )
            return (0, 0, False)

        if not dry_run and cands > 0:
            if not all_ok:
                log.warning(
                    "extract incomplete (cold pipeline errors), transcript not marked: %s", path
                )
            write_event(
                db,
                source="cli_extract",
                outcome="ok" if all_ok else "partial_failure",
                details={
                    "transcript": path.name,
                    "candidates_found": cands,
                    "rules_created": rules_created,
                    "all_ok": all_ok,
                    "project_id": project_id,
                },
            )
    finally:
        db.close()
    return (cands, rules_created, all_ok)


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
            try:
                cands, applied, _finished = _process_path(
                    path, project_id, cfg, dry_run=args.dry_run
                )
            except Exception as exc:
                print(f"nokori: extract failed for {path}: {exc}")
                return 1
            print(f"transcript: {path}")
            print(f"candidates: {cands}")
            if not args.dry_run:
                print(f"applied:    {applied}")
            return 0

        # Expire stale ingest jobs before processing
        db = open_db(cfg.db_path)
        try:
            try:
                expire_stale_ingest_jobs(db)
            except Exception as exc:
                log.warning("expire_stale_ingest_jobs failed (non-fatal): %s", exc)
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
                and abs(float(job_mtime) - float(current_mtime)) > TRANSCRIPT_MTIME_EPSILON_SEC
            ):
                new_job_path = job_io.refresh_job_mtime(
                    cfg,
                    job_path,
                    path,
                    job.get("project_id"),
                    current_mtime,
                )
                if new_job_path is None:
                    log.warning("refresh_job_mtime returned None for: %s", path)
                    continue
                job_path = new_job_path
                job = job_io.read_job(job_path)
                if not job:
                    continue
            try:
                cands, applied, finished = _process_path(
                    path, job.get("project_id"), cfg, dry_run=args.dry_run
                )
            except Exception as exc:
                log.warning("extract job failed: %s (%s)", job_path.name, exc)
                continue
            total_cands += cands
            total_applied += applied
            if not args.dry_run:
                if finished:
                    job_io.delete_job(job_path)
                else:
                    log.warning("extract job kept pending (not finished): %s", job_path.name)

        print(f"jobs:       {len(pending)}")
        print(f"candidates: {total_cands}")
        if not args.dry_run:
            print(f"applied:    {total_applied}")
        return 0
