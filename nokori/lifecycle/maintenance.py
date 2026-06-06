from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db import Db, _delete_rule_cascade_tx
from ..utils.logging import get_logger
from ..utils.time import iso_of, now_iso, parse_iso

log = get_logger("nokori.lifecycle.maintenance")

# Calendar days since created_at (not evidence active-days).
CANDIDATE_TTL_DAYS = 20
ANTI_PATTERN_TTL_DAYS = 40
CANDIDATE_CLEANUP_INTERVAL_DAYS = 30
UNMERGE_INTERVAL_DAYS = 90
# Dismiss looks back 24h; keep extra history for gate hash / debugging.
INJECTION_RETENTION_DAYS = 30
INJECTION_CLEANUP_INTERVAL_DAYS = 7
# Transition evaluation interval
TRANSITION_EVAL_INTERVAL_DAYS = 1


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


def run_candidate_cleanup(db: Db) -> int:
    if not _due(db, "candidate_cleanup", CANDIDATE_CLEANUP_INTERVAL_DAYS):
        return 0
    rows = db.fetchall(
        "SELECT id, source_origin, created_at FROM rules WHERE status = 'candidate'"
    )
    deleted = 0
    for r in rows:
        ttl = ANTI_PATTERN_TTL_DAYS if r["source_origin"] == "external_source_material" else CANDIDATE_TTL_DAYS
        age = _days_since_iso(r["created_at"])
        if age is not None and age >= ttl:
            with db.transaction() as tx:
                still = tx.execute(
                    "SELECT id FROM rules WHERE id = ? AND status = 'candidate'",
                    (r["id"],),
                ).fetchone()
                if not still:
                    continue
                _delete_rule_cascade_tx(tx, r["id"])
                deleted += 1
    _set_last_run(db, "candidate_cleanup")
    if deleted:
        log.info("candidate_cleanup deleted=%d", deleted)
        restored = _unmerge_orphan_replaced(db)
        if restored:
            log.info("candidate_cleanup unmerge_orphans restored=%d", restored)
    return deleted


def _unmerge_orphan_replaced(db: Db) -> int:
    """Restore archived rules whose replacement_id target no longer exists."""
    rows = db.fetchall(
        "SELECT id, replacement_id FROM rules WHERE status = 'archived' "
        "AND replacement_id IS NOT NULL"
    )
    restored = 0
    ts = now_iso()
    for r in rows:
        target = db.fetchone(
            "SELECT status FROM rules WHERE id = ?", (r["replacement_id"],)
        )
        if target is None:
            with db.transaction() as tx:
                cur = tx.execute(
                    "UPDATE rules SET status = 'candidate', replacement_id = NULL, "
                    "archived_reason = NULL, updated_at = ?, "
                    "rule_version = rule_version + 1 "
                    "WHERE id = ? AND status = 'archived'",
                    (ts, r["id"]),
                )
                if cur.rowcount == 0:
                    continue
            restored += 1
    return restored


def run_injection_cleanup(db: Db) -> int:
    """Cleanup old fire events (replaces legacy injections table cleanup)."""
    if not _due(db, "injection_cleanup", INJECTION_CLEANUP_INTERVAL_DAYS):
        return 0
    cutoff = _now() - timedelta(days=INJECTION_RETENTION_DAYS)
    cutoff_iso = iso_of(cutoff)
    with db.transaction() as tx:
        # Delete posthoc jobs referencing old fire events
        tx.execute(
            "DELETE FROM posthoc_jobs WHERE fire_event_id IN "
            "(SELECT id FROM rule_fire_events WHERE created_at < ?)",
            (cutoff_iso,),
        )
        cur = tx.execute(
            "DELETE FROM rule_fire_events WHERE created_at < ?",
            (cutoff_iso,),
        )
        deleted = cur.rowcount if cur.rowcount is not None else 0
    _set_last_run(db, "injection_cleanup")
    if deleted:
        log.info("injection_cleanup fire_events_deleted=%d", deleted)
    return deleted


def run_unmerge_check(db: Db) -> int:
    if not _due(db, "unmerge_check", UNMERGE_INTERVAL_DAYS):
        return 0
    rows = db.fetchall(
        "SELECT id, replacement_id FROM rules WHERE status = 'archived' "
        "AND replacement_id IS NOT NULL"
    )
    restored = 0
    ts = now_iso()
    for r in rows:
        target = db.fetchone(
            "SELECT status FROM rules WHERE id = ?", (r["replacement_id"],)
        )
        if target is None:
            with db.transaction() as tx:
                cur = tx.execute(
                    "UPDATE rules SET status = 'candidate', replacement_id = NULL, "
                    "archived_reason = NULL, updated_at = ?, "
                    "rule_version = rule_version + 1 "
                    "WHERE id = ? AND status = 'archived'",
                    (ts, r["id"]),
                )
                if cur.rowcount == 0:
                    continue
            restored += 1
            continue
        if target["status"] in ("suppressed", "archived"):
            with db.transaction() as tx:
                cur = tx.execute(
                    "UPDATE rules SET status = 'candidate', replacement_id = NULL, "
                    "archived_reason = NULL, updated_at = ?, "
                    "rule_version = rule_version + 1 "
                    "WHERE id = ? AND status = 'archived'",
                    (ts, r["id"]),
                )
                if cur.rowcount == 0:
                    continue
            restored += 1
    _set_last_run(db, "unmerge_check")
    if restored:
        log.info("unmerge_check restored=%d", restored)
    return restored


def run_maintenance(db: Db, cfg=None) -> dict:
    """Run all due maintenance tasks, delegating transitions to lifecycle.transitions."""
    from .transitions import run_all_pending_transitions

    transition_results: list = []
    if _due(db, "transition_eval", TRANSITION_EVAL_INTERVAL_DAYS):
        transition_results = run_all_pending_transitions(db)
        _set_last_run(db, "transition_eval")
        applied = sum(1 for r in transition_results if r.applied)
        if applied:
            log.info("transition_eval applied=%d total=%d", applied, len(transition_results))

    return run_due_jobs(db, cfg, transition_results)



def run_session_file_cleanup(cfg) -> int:
    from ..utils import sessions

    return sessions.prune_ended_session_files(cfg)


def run_due_jobs(db: Db, cfg=None, transition_results=None) -> dict:
    session_cleanup = 0
    coalesce_cleanup = 0
    prompt_ack_cleanup = 0
    if cfg is not None:
        from ..extract import jobs as job_io
        from ..gate import prompt_ack
        from ..hooks.coalesce import prune_stale_claims

        job_io.quarantine_corrupt_jobs(cfg)
        session_cleanup = run_session_file_cleanup(cfg)
        coalesce_cleanup = prune_stale_claims(cfg)
        prompt_ack_cleanup = prompt_ack.prune_stale(cfg)

    transitions_applied = 0
    if transition_results:
        transitions_applied = sum(1 for r in transition_results if r.applied)

    summary = {
        "candidate_cleanup": run_candidate_cleanup(db),
        "injection_cleanup": run_injection_cleanup(db),
        "unmerge_check": run_unmerge_check(db),
        "transitions_applied": transitions_applied,
        "session_cleanup": session_cleanup,
        "coalesce_cleanup": coalesce_cleanup,
        "prompt_ack_cleanup": prompt_ack_cleanup,
    }
    return summary
