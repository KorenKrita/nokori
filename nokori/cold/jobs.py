"""Durable cold-path job runner.

Handles LLM job idempotency, circuit breakers, and transcript ingest
job lifecycle. Jobs are keyed by role + prompt_version + model_id +
input_hash for deduplication and caching.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from nokori.db import Db
from nokori.utils.time import iso_of as _iso_of, local_now, now_iso as _shared_now_iso, parse_iso

# --- Constants ---

CIRCUIT_BREAKER_RATE_THRESHOLD = 0.50  # role_failure_rate >= 0.50 trips breaker (spec 5.2)
CIRCUIT_BREAKER_SAMPLE_SIZE = 10  # last N attempts to evaluate
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 300
TRANSCRIPT_INGEST_TTL_HOURS = 72
SCHEMA_PARSE_FAILURE_CONSECUTIVE_MAX = 3
PROVIDER_AUTH_RATE_LIMIT_ERRORS = ("auth_error", "rate_limit", "401", "429")
PROVIDER_AUTH_SAMPLE_SIZE = 5
MAX_JOB_RETRIES = 6


# --- Helpers ---


def _now_iso() -> str:
    return _shared_now_iso()


def _retry_backoff_seconds(retries: int) -> int:
    """Exponential backoff capped at 15 minutes."""
    return min(30 * (2**retries), 900)


# --- LLM Job Functions ---


def enqueue_job(
    db: Db,
    role: str,
    model_id: str,
    prompt_version: str,
    input_hash: str,
) -> str:
    """Enqueue an LLM job with idempotency.

    If a job with the same key exists and is done, returns cached job id.
    If pending or failed, returns existing job id for retry tracking.
    Otherwise creates a new job. Returns job id.
    """
    job_id = str(uuid.uuid4())
    now = _now_iso()
    with db.transaction() as tx:
        existing = tx.execute(
            "SELECT id, status, output_json FROM llm_jobs "
            "WHERE role = ? AND model_id = ? AND prompt_version = ? AND input_hash = ?",
            (role, model_id, prompt_version, input_hash),
        ).fetchone()
        if existing is not None:
            return existing["id"]
        tx.execute(
            "INSERT INTO llm_jobs (id, role, model_id, prompt_version, input_hash, "
            "status, retries, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
            (job_id, role, model_id, prompt_version, input_hash, now, now),
        )
    return job_id


def get_cached_output(
    db: Db,
    role: str,
    model_id: str,
    prompt_version: str,
    input_hash: str,
) -> str | None:
    """Return output_json if a completed job exists with this key."""
    row = db.fetchone(
        "SELECT output_json FROM llm_jobs "
        "WHERE role = ? AND model_id = ? AND prompt_version = ? AND input_hash = ? "
        "AND status = 'done'",
        (role, model_id, prompt_version, input_hash),
    )
    if row is None:
        return None
    return row["output_json"]


def mark_job_complete(db: Db, job_id: str, output_json: str) -> None:
    """Update job status to done with output."""
    now = _now_iso()
    with db.transaction() as tx:
        tx.execute(
            "UPDATE llm_jobs SET status = 'done', output_json = ?, updated_at = ? WHERE id = ?",
            (output_json, now, job_id),
        )


def mark_job_failed(db: Db, job_id: str, error_info: str | None = None) -> None:
    """Increment retries, set next_retry_at. Stores error_info in output_json for circuit breaker classification."""
    now = _now_iso()
    with db.transaction() as tx:
        row = tx.execute("SELECT role, retries FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        new_retries = row["retries"] + 1
        if new_retries > MAX_JOB_RETRIES:
            tx.execute(
                "UPDATE llm_jobs SET status = 'dead', output_json = ?, updated_at = ? WHERE id = ?",
                (error_info, now, job_id),
            )
            return
        backoff = _retry_backoff_seconds(new_retries)
        next_retry_iso = _iso_of(local_now() + timedelta(seconds=backoff))
        tx.execute(
            "UPDATE llm_jobs SET status = 'failed', retries = ?, "
            "next_retry_at = ?, output_json = ?, updated_at = ? WHERE id = ?",
            (new_retries, next_retry_iso, error_info, now, job_id),
        )


def is_circuit_breaker_open(db: Db, role: str, model_id: str | None = None) -> bool:
    """Check if circuit breaker is open for role/provider/schema failures.

    Three breaker types (spec section 5.2):
    1. role_failure_rate >= 0.50 over last 10 attempts -> pause role
    2. provider_auth_or_rate_limit_error -> pause affected provider/model route
    3. schema_parse_failure >= 3 consecutive -> pause role prompt version
    """
    # Type 1: role_failure_rate >= 0.50 over last 10 attempts -> pause (spec 5.2)
    rows = db.fetchall(
        "SELECT status, updated_at FROM llm_jobs WHERE role = ? ORDER BY updated_at DESC LIMIT ?",
        (role, CIRCUIT_BREAKER_SAMPLE_SIZE),
    )
    if len(rows) >= CIRCUIT_BREAKER_SAMPLE_SIZE:
        failure_count = sum(1 for r in rows if r["status"] == "failed")
        total = len(rows)
        failure_rate = failure_count / total if total > 0 else 0.0
        if failure_rate >= CIRCUIT_BREAKER_RATE_THRESHOLD:
            last_failure = next((r["updated_at"] for r in rows if r["status"] == "failed"), None)
            if last_failure:
                recent_dt = parse_iso(last_failure)
                if recent_dt is None:
                    return True
                elapsed = (local_now() - recent_dt).total_seconds()
                if elapsed < CIRCUIT_BREAKER_COOLDOWN_SECONDS:
                    return True

    # Type 2: provider auth/rate-limit (check by model_id if available)
    if model_id:
        provider_rows = db.fetchall(
            "SELECT status, output_json, updated_at FROM llm_jobs WHERE model_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (model_id, PROVIDER_AUTH_SAMPLE_SIZE),
        )
        auth_failures = sum(
            1
            for r in provider_rows
            if r["status"] == "failed" and _is_auth_rate_error(r["output_json"])
        )
        if auth_failures >= 2:
            # Check cooldown: if most recent auth failure is within cooldown, breaker open
            most_recent_auth = next(
                (
                    r["updated_at"]
                    for r in provider_rows
                    if r["status"] == "failed" and _is_auth_rate_error(r["output_json"])
                ),
                None,
            )
            if most_recent_auth:
                recent_dt = parse_iso(most_recent_auth)
                if recent_dt is None:
                    return True
                elapsed = (local_now() - recent_dt).total_seconds()
                if elapsed < CIRCUIT_BREAKER_COOLDOWN_SECONDS:
                    return True

    # Type 3: consecutive schema parse failures for this role+prompt_version
    from .roles import PROMPT_VERSIONS

    prompt_version = PROMPT_VERSIONS.get(role)
    if prompt_version:
        schema_rows = db.fetchall(
            "SELECT status, output_json FROM llm_jobs "
            "WHERE role = ? AND prompt_version = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (role, prompt_version, SCHEMA_PARSE_FAILURE_CONSECUTIVE_MAX),
        )
        if len(schema_rows) >= SCHEMA_PARSE_FAILURE_CONSECUTIVE_MAX:
            all_schema_fails = all(
                r["status"] == "failed" and _is_schema_parse_error(r["output_json"])
                for r in schema_rows
            )
            if all_schema_fails:
                return True

    return False


def _is_auth_rate_error(output_json: str | None) -> bool:
    if not output_json:
        return False
    lower = output_json.lower()
    return any(err in lower for err in PROVIDER_AUTH_RATE_LIMIT_ERRORS)


def _is_schema_parse_error(output_json: str | None) -> bool:
    if not output_json:
        return False
    lower = output_json.lower()
    return "schema" in lower or "invalid json" in lower or "validation failed" in lower


# --- Transcript Ingest Job Functions ---


def enqueue_transcript_ingest(
    db: Db,
    transcript_ref: str,
    segment_hash: str,
    extractor_prompt_version: str,
) -> str:
    """Create a transcript_ingest_jobs entry with TTL.

    Returns the job id. Deduplicates on segment_hash + extractor_prompt_version.
    """
    job_id = str(uuid.uuid4())
    now = _now_iso()
    ttl_expires_at = _iso_of(local_now() + timedelta(hours=TRANSCRIPT_INGEST_TTL_HOURS))

    with db.transaction() as tx:
        existing = tx.execute(
            "SELECT id FROM transcript_ingest_jobs "
            "WHERE segment_hash = ? AND extractor_prompt_version = ? "
            "AND status IN ('pending', 'done')",
            (segment_hash, extractor_prompt_version),
        ).fetchone()
        if existing is not None:
            return existing["id"]
        tx.execute(
            "INSERT INTO transcript_ingest_jobs "
            "(id, transcript_segment_ref, segment_hash, status, "
            "ttl_expires_at, extractor_prompt_version, retries, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, 0, ?, ?)",
            (
                job_id,
                transcript_ref,
                segment_hash,
                ttl_expires_at,
                extractor_prompt_version,
                now,
                now,
            ),
        )
    return job_id


def mark_ingest_done(db: Db, segment_hash: str, extractor_prompt_version: str) -> None:
    """Mark a transcript ingest job as done after cold pipeline succeeds."""
    now = _now_iso()
    with db.transaction() as tx:
        tx.execute(
            "UPDATE transcript_ingest_jobs SET status = 'done', updated_at = ? "
            "WHERE segment_hash = ? AND extractor_prompt_version = ? AND status = 'pending'",
            (now, segment_hash, extractor_prompt_version),
        )


def expire_stale_ingest_jobs(db: Db) -> int:
    """Mark expired transcript ingest jobs as 'expired'. Return count."""
    now = _now_iso()
    with db.transaction() as tx:
        cursor = tx.execute(
            "UPDATE transcript_ingest_jobs SET status = 'expired', "
            "updated_at = ? WHERE status = 'pending' AND ttl_expires_at <= ?",
            (now, now),
        )
        changed = cursor.rowcount
    return changed
