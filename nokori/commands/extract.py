from __future__ import annotations

import argparse
from pathlib import Path

from ..config import Config
from ..db import open_db
from ..extract import jobs as job_io
from ..extract.compressor import compress
from ..extract.extractor import extract as extract_candidates
from ..extract.merger import merge_candidate
from ..extract.reader import read as read_transcript
from ..lifecycle.hot_cache import mark_extracted
from ..llm.adapter import LLMAdapter


def _process_path(path: Path, project_id: str | None, cfg: Config,
                  *, dry_run: bool) -> tuple[int, int]:
    turns = read_transcript(path)
    text = compress(turns)
    if not text.strip():
        if not dry_run:
            db = open_db(cfg.db_path)
            try:
                mark_extracted(db, path, path.stat().st_mtime)
            finally:
                db.close()
        return (0, 0)
    llm = LLMAdapter(cfg)
    candidates = extract_candidates(text, llm)
    if dry_run:
        return (len(candidates), 0)

    db = open_db(cfg.db_path)
    try:
        merged = 0
        for cand in candidates:
            outcome = merge_candidate(cand, db, llm, project_id, cfg=cfg)
            merged += outcome.inserted + outcome.activated + outcome.superseded
        mark_extracted(db, path, path.stat().st_mtime)
    finally:
        db.close()
    return (len(candidates), merged)


def run(args: argparse.Namespace, cfg: Config) -> int:
    if args.session:
        path = Path(args.session).expanduser().resolve()
        if not path.exists():
            print(f"nokori: transcript not found: {path}")
            return 1
        cands, applied = _process_path(path, None, cfg, dry_run=args.dry_run)
        print(f"transcript: {path}")
        print(f"candidates: {cands}")
        if not args.dry_run:
            print(f"applied:    {applied}")
        return 0

    pending = job_io.list_pending(cfg)
    if not pending:
        print("(no pending extract jobs)")
        return 0

    total_cands = 0
    total_applied = 0
    for job_path in pending:
        job = job_io.read_job(job_path)
        if not job:
            job_io.delete_job(job_path)
            continue
        path = Path(job["transcript_path"])
        if not path.exists():
            job_io.delete_job(job_path)
            continue
        cands, applied = _process_path(
            path, job.get("project_id"), cfg, dry_run=args.dry_run
        )
        total_cands += cands
        total_applied += applied
        if not args.dry_run:
            job_io.delete_job(job_path)

    print(f"jobs:       {len(pending)}")
    print(f"candidates: {total_cands}")
    if not args.dry_run:
        print(f"applied:    {total_applied}")
    return 0
