from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..config import Config
from ..utils.time import now_iso


def transcript_hash(path: Path, mtime: float) -> str:
    raw = f"{path.resolve()}::{mtime:.6f}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _job_transcript_path(transcript_path: Path) -> str:
    return str(transcript_path.expanduser().resolve())


def write_job(cfg: Config, transcript_path: Path, project_id: str | None,
              mtime: float) -> Path:
    cfg.ensure_dirs()
    h = transcript_hash(transcript_path, mtime)
    path_str = _job_transcript_path(transcript_path)
    payload = {
        "transcript_path": path_str,
        "transcript_hash": h,
        "transcript_mtime": mtime,
        "project_id": project_id,
        "created_at": now_iso(),
        "status": "pending",
    }
    out = cfg.jobs_dir / f"extract-{h}.json"
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        existing.update({
            "transcript_path": path_str,
            "transcript_hash": h,
            "transcript_mtime": mtime,
            "project_id": project_id,
            "status": "pending",
        })
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)
        return out
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    return out


def quarantine_corrupt_job(path: Path, cfg: Config) -> None:
    """Move unreadable extract job files out of the pending queue."""
    bad_dir = cfg.jobs_dir / "bad"
    bad_dir.mkdir(parents=True, exist_ok=True)
    dest = bad_dir / path.name
    try:
        os.replace(path, dest)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass


def quarantine_corrupt_jobs(cfg: Config) -> int:
    """Scan jobs_dir and quarantine corrupt extract-*.json files."""
    if not cfg.jobs_dir.exists():
        return 0
    moved = 0
    for path in sorted(cfg.jobs_dir.glob("extract-*.json")):
        if read_job(path) is None:
            quarantine_corrupt_job(path, cfg)
            moved += 1
    return moved


def list_jobs(cfg: Config, *, status: str | None = "pending") -> list[Path]:
    """List extract job files; default status filters to pending only."""
    if not cfg.jobs_dir.exists():
        return []
    paths = sorted(cfg.jobs_dir.glob("extract-*.json"))
    if status is None:
        return paths
    out: list[Path] = []
    for path in paths:
        job = read_job(path)
        if job is None:
            quarantine_corrupt_job(path, cfg)
            continue
        if job.get("status", "pending") == status:
            out.append(path)
    return out


def find_project_id_for_transcript(cfg: Config, transcript_path: Path) -> str | None:
    """Best-effort project_id from any extract job pointing at this transcript."""
    resolved = _job_transcript_path(transcript_path)
    for job_path in list_jobs(cfg, status=None):
        job = read_job(job_path)
        if job and job.get("transcript_path") == resolved:
            pid = job.get("project_id")
            if pid:
                return str(pid)
    return None


def read_job(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def delete_job(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def refresh_job_mtime(
    cfg: Config, job_path: Path, transcript_path: Path,
    project_id: str | None, new_mtime: float,
) -> Path:
    """Re-queue job when transcript mtime changed after SessionEnd (keep pending)."""
    delete_job(job_path)
    return write_job(cfg, transcript_path, project_id, new_mtime)
