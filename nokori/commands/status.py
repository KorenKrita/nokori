from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..db import open_db
from ..lifecycle.promotion import (
    CROSS_PROJECT_PROMOTE_THRESHOLD,
    unique_promotion_project_ids,
)
from ..extract import jobs as job_io
from ..search import embed_ipc
from ..commands.install import describe_claude_hooks, describe_cursor_hooks
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
    db = open_db(cfg.db_path)
    try:
        rules = db.fetchall("SELECT status, COUNT(*) AS n FROM rules GROUP BY status")
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        injected_24h = db.fetchone(
            "SELECT COUNT(*) AS n FROM injections WHERE created_at >= ?",
            (cutoff,),
        )
        global_rules = db.fetchone(
            "SELECT COUNT(*) AS n FROM rules WHERE project_scope = 'global'"
        )
        promotion_rows = []
        if cfg.promotion_enabled:
            promotion_rows = db.fetchall(
                "SELECT short_id, project_id, trigger_text, promotion_evidence, "
                "shadow_hit_count FROM rules "
                "WHERE status = 'active' AND confidence = 'high' "
                "AND source_type IN ('correction','anti_pattern','solution') "
                "AND project_scope = 'project' AND project_id IS NOT NULL "
                "ORDER BY updated_at DESC"
            )
    finally:
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
    print(f"rules.total    {total}")
    print(f"rules.active   {by_status.get('active', 0)}")
    print(f"rules.dormant  {by_status.get('dormant', 0)}")
    print(f"rules.candidate {by_status.get('candidate', 0)}")
    print(f"rules.merged   {by_status.get('merged', 0)}")
    print(f"rules.archived {by_status.get('archived', 0)}")
    print(f"injections.last_24h {injected_24h['n'] if injected_24h else 0}")
    print(f"gate.enabled   {cfg.gate_enabled}")
    print(f"extract.mode   {cfg.extract_mode}")
    pending_jobs = len(job_io.list_jobs(cfg, status="pending"))
    print(f"extract.pending {pending_jobs}")
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
    print(f"promotion.threshold   {CROSS_PROJECT_PROMOTE_THRESHOLD}")
    print(f"rules.global          {global_rules['n'] if global_rules else 0}")

    if cfg.promotion_enabled:
        candidates: list[tuple[int, list[str], dict]] = []
        for row in promotion_rows:
            projects = unique_promotion_project_ids(row["promotion_evidence"])
            if not projects:
                continue
            candidates.append((len(projects), projects, row))
        candidates.sort(key=lambda x: (-x[0], x[2]["short_id"]))
        print(f"promotion.in_progress {len(candidates)}")
        for n_projects, projects, row in candidates[:10]:
            trigger = (row["trigger_text"] or "").strip()
            if len(trigger) > 48:
                trigger = trigger[:45] + "..."
            proj_list = ",".join(projects[:5])
            if len(projects) > 5:
                proj_list += ",..."
            print(
                f"  {row['short_id']}  {n_projects}/{CROSS_PROJECT_PROMOTE_THRESHOLD}  "
                f"shadow_hits={row['shadow_hit_count']}  "
                f"from={row['project_id']}  "
                f"projects=[{proj_list}]  "
                f"{trigger}"
            )
        if not candidates:
            print(
                "  (no shadow promotion progress yet — need other projects to HOT-hit "
                "project-scoped rules)"
            )
    else:
        print("promotion.in_progress (disabled)")

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
