# Parts 2-5 Implementation Notes

> These parts were implemented in the first session. This document records the key decisions, file changes, and patterns used, for reference in subsequent sessions.

---

## Part 2: Hot-Path Hook Instrumentation

**Commit:** `9aa2be91`

### Files Modified
- `nokori/hooks/session_start.py` — `_maybe_kickstart_embed` now returns status string; `handle` writes event with embed_status, maintenance_ok, hot_cache, rule_count
- `nokori/hooks/user_prompt_submit.py` — `handle` writes event at each exit point (no_rules, retrieve_failed, no_matches, injected); captures hot/warm counts, gate marker state, prompt snippet (200 chars)
- `nokori/hooks/pre_tool_use.py` — `_run_gate` returns 3-tuple (response, outcome, blocked_ids); `handle` calls `_write_pre_tool_event` helper which opens its own DB connection
- `nokori/hooks/session_end.py` — tracks posthoc_enqueued, extract_job_written, async_extract_spawned; writes event in separate DB connection

### Key Design Decisions
- **pre_tool_use opens separate DB connection** for event writing because `_run_gate` closes its own DB before returning (gate marker delete happens outside the try/finally)
- **user_prompt_submit uses fixed outcome "injected"** (not dynamic like "injected_3h_2w") to keep aggregation-friendly
- **session_start wraps total_rule_count in try/except** because it runs after DB might be in bad state from failed maintenance
- **gate_marker_written check guarded by cfg.gate_enabled** to avoid reporting marker written when gate is off

### Test File
- `tests/test_hook_observability.py` — 6 tests covering all 4 hooks basic behavior

---

## Part 3: Cold-Path & CLI Instrumentation

**Commit:** `608322b5`

### Files Modified
- `nokori/cold/pipeline.py` — `run_cold_pipeline` writes event on success (status, scores, rejection_reason, trigger_preview) and error_event on exceptions (circuit_breaker, timeout, connection, validation, runtime)
- `nokori/commands/extract.py` — `_process_path` writes event after processing each transcript (candidates_found, rules_created, all_ok)
- `nokori/commands/add.py` — writes event after successful rule insertion (short_id, status, trigger_preview)
- `nokori/commands/dismiss.py` — writes event after archival (short_id, rule_id)

### Key Design Decisions
- **Pipeline writes ONE event per candidate** (not per stage) — captures the final result with all metadata. Individual stage data flows through the existing `ColdPipelineResult` and existing `rule_reviews` table.
- **Error type classification**: timeout/connection/validation/runtime based on exception type
- **CLI events have session_id=NULL** (not associated with any Claude session)

---

## Part 4: Backend API

**Commit:** `3e6a8909`

### Files Created
- `nokori/web/api/timeline.py` — GET /api/timeline (paginated, filterable, has_more) + GET /api/timeline/sessions
- `nokori/web/api/monitor.py` — GET /api/monitor/overview + GET /api/monitor/errors (Literal group_by) + GET /api/monitor/errors/trend

### Files Modified
- `nokori/web/app.py` — register new routers

### Key Design Decisions
- **has_more pagination**: fetch `limit + 1` rows, return `limit`, use extra row existence as signal
- **group_by uses `Literal` type** for FastAPI schema self-documentation and 422 on invalid values
- **Overview aggregation**: real-time SQLite queries (no pre-computation), acceptable at 30-day retention scale
- **Funnel data**: sourced from hook_events where source='cold_pipeline', grouped by outcome

### Test File
- `tests/test_timeline_api.py` — 16 tests covering all endpoints, filters, pagination, empty states

---

## Part 5: CLI Commands

**Commit:** `c352eaee`

### Files Created
- `nokori/commands/report.py` — `nokori report` command (markdown default, `--json`, `--since`, `--session`)
- `nokori/commands/stream.py` — `nokori stream` command (dump + `--follow` mode, `--type`, `--session`, `--verbose`, `--limit`)

### Files Modified
- `nokori/cli.py` — registered both commands in parser + dispatch
- `nokori/events/observability.py` — added `since` parameter to `query_events`

### Key Design Decisions
- **report outputs markdown by default** (human + AI readable), JSON with `--json`
- **stream dump mode** exits after printing; **follow mode** polls every 5s
- **since filter applied at SQL level** (not post-filter in Python) — avoids limit/filter ordering bug
- **follow mode fallback**: when `last_id` is None (no initial events), passes `since` to polling query to avoid re-fetching old events

### Test File
- `tests/test_cli_report_stream.py` — 10 tests covering both commands

---

## Worktree Info

- **Path:** `/Users/korenkrita/Coding/nokori-observability`
- **Branch:** `feature/observability-timeline`
- **Base:** `48da629a` (main at time of branch creation)
- **Dev venv:** `/Users/korenkrita/Coding/nokori/.venv/bin/python`

## Test Command
```bash
cd /Users/korenkrita/Coding/nokori-observability && /Users/korenkrita/Coding/nokori/.venv/bin/python -m pytest tests/ -q
```

## Full Test Count After Part 5
934 passed, 1 warning
