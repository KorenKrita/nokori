from __future__ import annotations

from fastapi import APIRouter

from nokori.db import open_db
from nokori.extract import jobs as job_io
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/extract/jobs")
def list_extract_jobs():
    cfg = get_config()
    pending = job_io.list_jobs(cfg, status="pending")
    db = open_db(cfg.db_path)
    try:
        done_rows = db.fetchall(
            "SELECT transcript_path, extracted_at FROM extract_state "
            "WHERE status = 'done' ORDER BY extracted_at DESC LIMIT 50"
        )
    finally:
        db.close()
    return {
        "data": {
            "pending": [{"path": str(j)} for j in pending],
            "done": [
                {"path": row["transcript_path"], "extracted_at": row["extracted_at"]}
                for row in done_rows
            ],
        }
    }


@router.get("/extract/state")
def extract_state():
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall(
            "SELECT transcript_path, transcript_mtime, extracted_at, status, "
            "last_byte_offset FROM extract_state ORDER BY extracted_at DESC LIMIT 100"
        )
    finally:
        db.close()
    return {
        "data": [
            {
                "transcript_path": row["transcript_path"],
                "transcript_mtime": row["transcript_mtime"],
                "extracted_at": row["extracted_at"],
                "status": row["status"],
                "last_byte_offset": row["last_byte_offset"],
            }
            for row in rows
        ]
    }
