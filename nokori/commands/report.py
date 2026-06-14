"""nokori report — AI-friendly system status report.

Outputs structured markdown (default) or JSON with usage stats, rule stats,
error aggregation, and conversion metrics for a specified time range.
"""

from __future__ import annotations

import argparse
import json

from ..config import Config
from ..db import open_db
from ..events.observability import query_errors
from ..utils.time import local_days_ago, now_iso


def _default_since() -> str:
    return local_days_ago(7)


def run(args: argparse.Namespace, cfg: Config) -> int:
    if getattr(args, "metrics", False):
        ignored = [f for f in ("--since", "--session") if getattr(args, f.lstrip("-"), None)]
        if ignored:
            print(f"warning: {', '.join(ignored)} ignored with --metrics", flush=True)
        return _run_metrics(args, cfg)

    since = args.since or _default_since()
    session_id = args.session or None
    output_json = args.json

    db = open_db(cfg.db_path)
    try:
        data = _build_report(db, since=since, session_id=session_id)
    finally:
        db.close()

    if output_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_markdown(data)
    return 0


def _build_report(db, *, since: str, session_id: str | None) -> dict:
    where_parts = ["created_at >= ?"]
    params: list = [since]
    if session_id:
        where_parts.append("session_id = ?")
        params.append(session_id)
    where = " AND ".join(where_parts)

    total_events = db.fetchone(
        f"SELECT COUNT(*) AS n FROM hook_events WHERE {where}", tuple(params)
    )
    total_errors = db.fetchone(
        f"SELECT COUNT(*) AS n FROM error_events WHERE {where}", tuple(params)
    )

    sessions_row = db.fetchone(
        f"SELECT COUNT(DISTINCT session_id) AS n FROM hook_events WHERE {where} AND session_id IS NOT NULL",
        tuple(params),
    )

    events_by_source = db.fetchall(
        f"SELECT source, COUNT(*) AS count FROM hook_events WHERE {where} GROUP BY source ORDER BY count DESC",
        tuple(params),
    )

    events_by_outcome = db.fetchall(
        f"SELECT outcome, COUNT(*) AS count FROM hook_events WHERE {where} GROUP BY outcome ORDER BY count DESC LIMIT 15",
        tuple(params),
    )

    # Pipeline funnel
    pipeline_where = f"{where} AND source = 'cold_pipeline'"
    funnel_rows = db.fetchall(
        f"SELECT outcome, COUNT(*) AS count FROM hook_events WHERE {pipeline_where} GROUP BY outcome",
        tuple(params),
    )

    # Rule stats
    rule_stats = db.fetchall("SELECT status, COUNT(*) AS n FROM rules GROUP BY status")

    # Error breakdown
    error_by_role = query_errors(db, group_by="role", since=since, session_id=session_id)
    error_by_type = query_errors(db, group_by="error_type", since=since, session_id=session_id)
    error_by_model = query_errors(db, group_by="model_id", since=since, session_id=session_id)

    return {
        "generated_at": now_iso(),
        "since": since,
        "session_filter": session_id,
        "usage": {
            "total_events": total_events["n"] if total_events else 0,
            "total_errors": total_errors["n"] if total_errors else 0,
            "sessions": sessions_row["n"] if sessions_row else 0,
            "events_by_source": [dict(r) for r in events_by_source],
            "events_by_outcome": [dict(r) for r in events_by_outcome],
        },
        "pipeline_funnel": {r["outcome"]: r["count"] for r in funnel_rows},
        "rules": {r["status"]: r["n"] for r in rule_stats},
        "errors": {
            "by_role": error_by_role,
            "by_type": error_by_type,
            "by_model": error_by_model,
        },
    }


def _print_markdown(data: dict) -> None:
    print("# Nokori Report")
    print(f"Generated: {data['generated_at']}")
    print(f"Since: {data['since']}")
    if data["session_filter"]:
        print(f"Session: {data['session_filter']}")
    print()

    usage = data["usage"]
    print("## Usage")
    print(f"- Events: {usage['total_events']}")
    print(f"- Errors: {usage['total_errors']}")
    print(f"- Sessions: {usage['sessions']}")
    print()

    if usage["events_by_source"]:
        print("### Events by Source")
        for row in usage["events_by_source"]:
            print(f"  {row['source'] or '(unspecified)'}: {row['count']}")
        print()

    if usage["events_by_outcome"]:
        print("### Events by Outcome")
        for row in usage["events_by_outcome"]:
            print(f"  {row['outcome'] or '(unspecified)'}: {row['count']}")
        print()

    funnel = data["pipeline_funnel"]
    if funnel:
        print("## Pipeline Funnel")
        for outcome, count in sorted(funnel.items(), key=lambda x: -x[1]):
            print(f"  {outcome or '(unspecified)'}: {count}")
        print()

    rules = data["rules"]
    if rules:
        print("## Rules")
        for status, n in sorted(rules.items()):
            print(f"  {status}: {n}")
        print()

    errors = data["errors"]
    if any(errors.values()):
        print("## Errors")
        if errors["by_role"]:
            print("### By Role")
            for row in errors["by_role"]:
                print(f"  {row['role'] or '(unspecified)'}: {row['count']}")
        if errors["by_type"]:
            print("### By Type")
            for row in errors["by_type"]:
                print(f"  {row['error_type'] or '(unspecified)'}: {row['count']}")
        if errors["by_model"]:
            print("### By Model")
            for row in errors["by_model"]:
                print(f"  {row['model_id'] or '(unspecified)'}: {row['count']}")


def _run_metrics(args: argparse.Namespace, cfg: Config) -> int:
    output_json = getattr(args, "json", False)

    db = open_db(cfg.db_path)
    try:
        data = _build_metrics(db)
    finally:
        db.close()

    if output_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_metrics(data)
    return 0


def _build_metrics(db) -> dict:
    since_30d = local_days_ago(30)
    since_7d = local_days_ago(7)

    # Extraction stats
    extract_rows = db.fetchall(
        "SELECT status, COUNT(*) AS n FROM extract_state GROUP BY status"
    )
    extraction = {row["status"]: row["n"] for row in extract_rows}

    # Rule counts by status
    rule_rows = db.fetchall("SELECT status, COUNT(*) AS n FROM rules GROUP BY status")
    rules = {row["status"]: row["n"] for row in rule_rows}

    # Posthoc labels (30 days)
    posthoc_rows = db.fetchall(
        "SELECT posthoc_label, COUNT(*) AS n FROM rule_fire_events "
        "WHERE posthoc_label IS NOT NULL AND created_at >= ? GROUP BY posthoc_label",
        (since_30d,),
    )
    posthoc = {row["posthoc_label"]: row["n"] for row in posthoc_rows}

    # Gate blocks (7 days)
    gate_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM hook_events "
        "WHERE source = 'pre_tool_use' AND outcome = 'blocked' AND created_at >= ?",
        (since_7d,),
    )
    gate_blocks = gate_row["n"] if gate_row else 0

    return {
        "extraction": extraction,
        "rules": rules,
        "posthoc_30d": posthoc,
        "gate_blocks_7d": gate_blocks,
    }


def _print_metrics(data: dict) -> None:
    print("=== Cold-Path Quality Metrics ===")
    print()

    ext = data["extraction"]
    done = ext.get("done", 0)
    pending = ext.get("pending", 0)
    failed = ext.get("failed", 0)
    print(f"Extraction: {done} done, {pending} pending, {failed} failed")

    rules = data["rules"]
    candidate = rules.get("candidate", 0)
    active = rules.get("active", 0)
    trusted = rules.get("trusted", 0)
    suppressed = rules.get("suppressed", 0)
    archived = rules.get("archived", 0)
    print(
        f"Rules: {candidate} candidate, {active} active, {trusted} trusted, "
        f"{suppressed} suppressed, {archived} archived"
    )

    ph = data["posthoc_30d"]
    useful = ph.get("observed_useful", 0) + ph.get("plausible_useful", 0)
    irrelevant = ph.get("irrelevant", 0)
    harmful = ph.get("harmful", 0)
    unclear = ph.get("unclear", 0)
    print(
        f"Injection quality (30d): {useful} useful, {irrelevant} irrelevant, "
        f"{harmful} harmful, {unclear} unclear"
    )

    print(f"Gate: {data['gate_blocks_7d']} blocks in last 7 days")
