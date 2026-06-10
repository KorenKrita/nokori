from __future__ import annotations

import argparse

from ..config import Config
from ..cold.jobs import is_circuit_breaker_open
from ..db import open_db, fetch_rules
from ..utils.time import local_hours_ago
from ..extract import jobs as job_io
from ..search import embed_ipc
from ..search.idf_stats import build_idf_stats
from ..commands.install import (
    describe_claude_hooks,
    describe_cursor_hooks,
    describe_dual_hook_registration,
)
from ..hooks.coalesce import coalesce_enabled
from ..utils import sessions


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _print_hook_platform(label: str, state: dict[str, object], *, disable_hint: str) -> None:
    installed = bool(state.get("installed"))
    print(f"hooks.{label}.installed { _yn(installed) }")
    if label == "claude":
        disabled = bool(state.get("disabled"))
        print(f"hooks.{label}.disabled  { _yn(disabled) if installed else 'n/a' }")
        if disabled:
            print(
                f"hooks.{label}.note      NOKORI_DISABLED in settings.json env "
                "(hooks still registered; nokori install --enable)"
            )
        elif not installed:
            note = state.get("note")
            if note:
                print(f"hooks.{label}.note      {note}")
    else:
        print(f"hooks.{label}.disabled  n/a ({disable_hint})")
        if not installed:
            note = state.get("note")
            if note:
                print(f"hooks.{label}.note      {note}")
    path = state.get("path")
    if path:
        print(f"hooks.{label}.path      {path}")


def run(_args: argparse.Namespace, cfg: Config) -> int:
    db = None
    try:
        db = open_db(cfg.db_path)
        rules = db.fetchall("SELECT status, COUNT(*) AS n FROM rules GROUP BY status")
        cutoff = local_hours_ago(24)
        injected_24h = db.fetchone(
            "SELECT COUNT(*) AS n FROM rule_fire_events WHERE created_at >= ?",
            (cutoff,),
        )
        global_rules = db.fetchone(
            "SELECT COUNT(*) AS n FROM rules WHERE project_scope = 'global'"
        )

        # Pending cold/posthoc jobs
        pending_cold = db.fetchone(
            "SELECT COUNT(*) AS n FROM llm_jobs WHERE status IN ('pending','failed')"
        )
        pending_posthoc = db.fetchone(
            "SELECT COUNT(*) AS n FROM posthoc_jobs WHERE status = 'pending'"
        )

        # Circuit breaker states
        cb_roles = db.fetchall(
            "SELECT DISTINCT role FROM llm_jobs ORDER BY role"
        )
        cb_states: dict[str, bool] = {}
        for r in cb_roles:
            cb_states[r["role"]] = is_circuit_breaker_open(db, r["role"])

        # IDF pool stats
        eligible_rules = fetch_rules(db, statuses=("active", "trusted"))
        idf_stats = build_idf_stats(eligible_rules)

        promotion_rows = []
        if cfg.promotion_enabled:
            promotion_rows = db.fetchall(
                "SELECT short_id, project_id, trigger_canonical FROM rules "
                "WHERE status = 'active' "
                "AND project_scope = 'project' AND project_id IS NOT NULL "
                "ORDER BY updated_at DESC"
            )
    finally:
        if db is not None:
            db.close()

    by_status = {r["status"]: r["n"] for r in rules}
    total = sum(by_status.values())

    print(f"data_dir       {cfg.data_dir}")
    print(f"db             {cfg.db_path}")
    print(f"config.disabled {cfg.disabled}")
    _print_hook_platform(
        "claude",
        describe_claude_hooks(),
        disable_hint="",
    )
    _print_hook_platform(
        "cursor",
        describe_cursor_hooks(),
        disable_hint="use: nokori install --uninstall --cursor",
    )
    dual = describe_dual_hook_registration()
    print(f"hooks.duplicate_risk  {_yn(bool(dual.get('both_installed')))}")
    print(f"hooks.coalesce        {'on' if coalesce_enabled() else 'off'}")
    if dual.get("note"):
        print(f"hooks.duplicate_note  {dual['note']}")
    print(f"rules.total    {total}")
    print(f"rules.active   {by_status.get('active', 0)}")
    print(f"rules.trusted  {by_status.get('trusted', 0)}")
    print(f"rules.candidate {by_status.get('candidate', 0)}")
    print(f"rules.suppressed {by_status.get('suppressed', 0)}")
    print(f"rules.archived {by_status.get('archived', 0)}")
    print(f"fire_events.last_24h {injected_24h['n'] if injected_24h else 0}")
    print(f"gate.enabled   {cfg.gate_enabled}")
    print(f"extract.mode   {cfg.extract_mode}")
    pending_jobs = len(job_io.list_jobs(cfg, status="pending"))
    print(f"extract.pending {pending_jobs}")

    # Pending cold/posthoc jobs
    print(f"cold.outstanding {pending_cold['n'] if pending_cold else 0}")
    print(f"posthoc.pending {pending_posthoc['n'] if pending_posthoc else 0}")

    # Circuit breaker states
    open_breakers = [role for role, is_open in cb_states.items() if is_open]
    print(f"circuit_breakers.open  {len(open_breakers)}")
    for role in open_breakers:
        print(f"  {role}: OPEN (paused)")

    # IDF pool stats summary
    print(f"idf.pool_size          {idf_stats.rule_pool_size}")
    print(f"idf.pool_version       {idf_stats.pool_version}")
    print(f"idf.dynamic_threshold  {idf_stats.dynamic_threshold:.3f}")
    print(f"idf.unique_tokens      {len(idf_stats.df_by_token)}")

    print(f"llm.configured {bool(cfg.llm_base_url and cfg.llm_model)}")
    print(f"embed.configured {bool(cfg.embed_base_url and cfg.embed_model)}")
    print(f"hot_cache.enabled {cfg.hot_cache_enabled}")
    print(f"embed.hook_timeout_s {cfg.embed_hook_timeout_seconds}")
    est = embed_ipc.server_status(cfg)
    print(f"embed.server      {'running' if est['running'] else 'stopped'}")
    print(f"embed.server_pid  {est['pid']}")
    print(f"embed.server_idle {est['idle_seconds']}s")
    print(f"session.idle_s    {cfg.session_idle_seconds}")
    print(f"promotion.enabled   {cfg.promotion_enabled}")
    print(f"rules.global          {global_rules['n'] if global_rules else 0}")

    if cfg.promotion_enabled:
        print(f"promotion.project_scoped_active {len(promotion_rows)}")
        for row in promotion_rows[:10]:
            trigger = (row["trigger_canonical"] or "").strip()
            if len(trigger) > 48:
                trigger = trigger[:45] + "..."
            print(
                f"  {row['short_id']}  from={row['project_id']}  {trigger}"
            )
        if not promotion_rows:
            print("  (no project-scoped active rules)")
    else:
        print("promotion.enabled (disabled)")

    session_records = sessions.list_session_records(cfg)
    open_sess = [
        d for d in session_records if sessions.is_session_open(d)
    ]
    active = sessions.list_active_sessions(cfg, records=session_records)
    print(f"sessions.open     {len(open_sess)} (no SessionEnd)")
    print(f"sessions.active   {len(active)} (open + idle window)")
    for row in active[:5]:
        print(
            f"  {row.get('session_id', '?')[:24]}  "
            f"project={row.get('project_id') or '-'}  "
            f"last={row.get('last_activity', '-')}"
        )
    return 0
