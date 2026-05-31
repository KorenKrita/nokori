from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db import Db, delete_rule_cascade
from ..utils.logging import get_logger
from ..utils.sql_batch import batched
from ..utils.time import now_iso, parse_iso

log = get_logger("nokori.lifecycle.maintenance")

DORMANT_AFTER_DAYS = 30
DORMANT_SCAN_INTERVAL_DAYS = 7
# Calendar days since created_at (not evidence active-days).
CANDIDATE_TTL_DAYS = 20
ANTI_PATTERN_TTL_DAYS = 40
CANDIDATE_CLEANUP_INTERVAL_DAYS = 30
UNMERGE_INTERVAL_DAYS = 90
# Dismiss looks back 24h; keep extra history for gate hash / debugging.
INJECTION_RETENTION_DAYS = 30
INJECTION_CLEANUP_INTERVAL_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_since_iso(iso: str | None) -> int | None:
    dt = parse_iso(iso)
    if dt is None:
        return None
    return max(0, (_now() - dt).days)


def _last_run(db: Db, key: str) -> str | None:
    row = db.fetchone("SELECT last_run FROM maintenance_meta WHERE key = ?", (key,))
    return row["last_run"] if row else None


def _set_last_run(db: Db, key: str) -> None:
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO maintenance_meta (key, last_run) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET last_run = excluded.last_run",
            (key, now),
        )


def _due(db: Db, key: str, interval_days: int) -> bool:
    age = _days_since_iso(_last_run(db, key))
    return age is None or age >= interval_days


def run_dormant_scan(db: Db) -> int:
    if not _due(db, "dormant_scan", DORMANT_SCAN_INTERVAL_DAYS):
        return 0
    rows = db.fetchall(
        "SELECT id, last_hit, created_at FROM rules WHERE status = 'active'"
    )
    cutoff_days = DORMANT_AFTER_DAYS
    ts = now_iso()
    to_dormant: list[str] = []
    for r in rows:
        last_seen = r["last_hit"] or r["created_at"]
        age = _days_since_iso(last_seen)
        if age is not None and age >= cutoff_days:
            to_dormant.append(r["id"])
    moved = len(to_dormant)
    if to_dormant:
        with db.transaction() as tx:
            for batch in batched(to_dormant):
                placeholders = ",".join("?" * len(batch))
                tx.execute(
                    f"UPDATE rules SET status = 'dormant', updated_at = ? "
                    f"WHERE id IN ({placeholders})",
                    (ts, *batch),
                )
    _set_last_run(db, "dormant_scan")
    if moved:
        log.info("dormant_scan moved=%d", moved)
    return moved


def run_candidate_cleanup(db: Db) -> int:
    if not _due(db, "candidate_cleanup", CANDIDATE_CLEANUP_INTERVAL_DAYS):
        return 0
    rows = db.fetchall(
        "SELECT id, source_type, created_at FROM rules WHERE status = 'candidate'"
    )
    deleted = 0
    for r in rows:
        ttl = ANTI_PATTERN_TTL_DAYS if r["source_type"] == "anti_pattern" else CANDIDATE_TTL_DAYS
        age = _days_since_iso(r["created_at"])
        if age is not None and age >= ttl:
            with db.transaction() as tx:
                still = tx.execute(
                    "SELECT id FROM rules WHERE id = ? AND status = 'candidate'",
                    (r["id"],),
                ).fetchone()
                if not still:
                    continue
                tx.execute("DELETE FROM injections WHERE rule_id = ?", (r["id"],))
                tx.execute(
                    "DELETE FROM rule_embeddings WHERE rule_id = ?", (r["id"],)
                )
                cur = tx.execute(
                    "DELETE FROM rules WHERE id = ? AND status = 'candidate'",
                    (r["id"],),
                )
                if cur.rowcount:
                    deleted += 1
    _set_last_run(db, "candidate_cleanup")
    if deleted:
        log.info("candidate_cleanup deleted=%d", deleted)
        restored = _unmerge_orphan_merged(db)
        if restored:
            log.info("candidate_cleanup unmerge_orphans restored=%d", restored)
    return deleted


def _unmerge_orphan_merged(db: Db) -> int:
    """Restore merged rules whose superseded_by target no longer exists."""
    rows = db.fetchall(
        "SELECT id, superseded_by FROM rules WHERE status = 'merged' "
        "AND superseded_by IS NOT NULL"
    )
    restored = 0
    ts = now_iso()
    for r in rows:
        target = db.fetchone(
            "SELECT status FROM rules WHERE id = ?", (r["superseded_by"],)
        )
        if target is None:
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rules SET status = 'dormant', superseded_by = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (ts, r["id"]),
                )
            restored += 1
    return restored


def run_injection_cleanup(db: Db) -> int:
    if not _due(db, "injection_cleanup", INJECTION_CLEANUP_INTERVAL_DAYS):
        return 0
    cutoff = _now() - timedelta(days=INJECTION_RETENTION_DAYS)
    cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    with db.transaction() as tx:
        cur = tx.execute(
            "DELETE FROM injections WHERE created_at < ?",
            (cutoff_iso,),
        )
        deleted = cur.rowcount if cur.rowcount is not None else 0
    _set_last_run(db, "injection_cleanup")
    if deleted:
        log.info("injection_cleanup deleted=%d", deleted)
    return deleted


def run_unmerge_check(db: Db) -> int:
    if not _due(db, "unmerge_check", UNMERGE_INTERVAL_DAYS):
        return 0
    rows = db.fetchall(
        "SELECT id, superseded_by FROM rules WHERE status = 'merged' "
        "AND superseded_by IS NOT NULL"
    )
    restored = 0
    ts = now_iso()
    for r in rows:
        target = db.fetchone(
            "SELECT status FROM rules WHERE id = ?", (r["superseded_by"],)
        )
        if target is None:
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rules SET status = 'dormant', superseded_by = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (ts, r["id"]),
                )
            restored += 1
            continue
        if target["status"] in ("dormant", "archived"):
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE rules SET status = 'dormant', superseded_by = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (ts, r["id"]),
                )
            restored += 1
    _set_last_run(db, "unmerge_check")
    if restored:
        log.info("unmerge_check restored=%d", restored)
    return restored


def reactivate_dormant_on_retrieval_hot(db: Db, rule_id: str) -> None:
    ts = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET status = 'active', last_hit = ?, "
            "hit_count = hit_count + 1, updated_at = ? "
            "WHERE id = ? AND status = 'dormant'",
            (ts, ts, rule_id),
        )


def run_session_file_cleanup(cfg) -> int:
    from ..utils import sessions

    return sessions.prune_ended_session_files(cfg)


def run_due_jobs(db: Db, cfg=None) -> dict:
    session_cleanup = 0
    if cfg is not None:
        from ..extract import jobs as job_io

        job_io.quarantine_corrupt_jobs(cfg)
        session_cleanup = run_session_file_cleanup(cfg)
    summary = {
        "dormant_scan": run_dormant_scan(db),
        "candidate_cleanup": run_candidate_cleanup(db),
        "injection_cleanup": run_injection_cleanup(db),
        "unmerge_check": run_unmerge_check(db),
        "session_cleanup": session_cleanup,
    }
    return summary
