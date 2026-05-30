from __future__ import annotations

from datetime import datetime, timezone

from ..db import Db, delete_rule_cascade
from ..utils.logging import get_logger
from ..utils.time import now_iso, parse_iso

log = get_logger("nokori.lifecycle.maintenance")

DORMANT_AFTER_DAYS = 30
DORMANT_SCAN_INTERVAL_DAYS = 7
# Calendar days since created_at (not evidence active-days).
CANDIDATE_TTL_DAYS = 20
ANTI_PATTERN_TTL_DAYS = 40
CANDIDATE_CLEANUP_INTERVAL_DAYS = 30
UNMERGE_INTERVAL_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_since_iso(iso: str | None) -> int | None:
    dt = parse_iso(iso)
    if dt is None:
        return None
    return (_now() - dt).days


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
        placeholders = ",".join("?" * len(to_dormant))
        with db.transaction() as tx:
            tx.execute(
                f"UPDATE rules SET status = 'dormant', updated_at = ? "
                f"WHERE id IN ({placeholders})",
                (ts, *to_dormant),
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
            delete_rule_cascade(db, r["id"])
            deleted += 1
    _set_last_run(db, "candidate_cleanup")
    if deleted:
        log.info("candidate_cleanup deleted=%d", deleted)
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
            "UPDATE rules SET status = 'active', last_hit = ?, updated_at = ? "
            "WHERE id = ? AND status = 'dormant'",
            (ts, ts, rule_id),
        )


def run_due_jobs(db: Db) -> dict:
    summary = {
        "dormant_scan": run_dormant_scan(db),
        "candidate_cleanup": run_candidate_cleanup(db),
        "unmerge_check": run_unmerge_check(db),
    }
    return summary
