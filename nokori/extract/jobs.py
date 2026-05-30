from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..config import Config
from ..utils.time import now_iso


def transcript_hash(path: Path, mtime: float) -> str:
    raw = f"{path.resolve()}::{int(mtime)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def write_job(cfg: Config, transcript_path: Path, project_id: str | None,
              mtime: float) -> Path:
    cfg.ensure_dirs()
    h = transcript_hash(transcript_path, mtime)
    payload = {
        "transcript_path": str(transcript_path),
        "transcript_hash": h,
        "transcript_mtime": mtime,
        "project_id": project_id,
        "created_at": now_iso(),
        "status": "pending",
    }
    out = cfg.jobs_dir / f"extract-{h}.json"
    if out.exists():
        return out
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    return out


def list_pending(cfg: Config) -> list[Path]:
    if not cfg.jobs_dir.exists():
        return []
    return sorted(cfg.jobs_dir.glob("extract-*.json"))


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
