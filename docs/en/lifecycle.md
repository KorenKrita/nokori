# Rule Lifecycle

[← Back to main README](../../README.md)

---

## State machine

```
candidate → active → trusted
      │          │         │
      └──────────┴─────────┴→ suppressed → candidate (only by recovery automation)
                              └→ archived (terminal)
```

| Status | In reminders? | Gated? | How it got here |
|--------|---------------|--------|-----------------|
| `candidate` | No; shadow/evidence only | No | `nokori add` or cold extraction |
| `active` | Yes; WARM until usefulness is observed | No direct gate | Cold-path fast lane or shadow evidence |
| `trusted` | Yes | Maybe, only when `severity=gate_eligible` | Autonomous lifecycle after observed usefulness |
| `suppressed` | No; shadow recovery only | No | False-positive/harm evidence |
| `archived` | No | No | User dismiss or archival policy |

---

## How a rule turns active / trusted

- **Manual `nokori add` always creates a `candidate`**. Even `--severity high_risk` does not bypass the lifecycle.
- **Cold-path fast-lane to active** requires matcher compilation, archived-fingerprint checks, merge policy, synthetic evaluation, and cold-fast-lane thresholds.
- **Candidate → active promotion** via lifecycle uses shadow evidence; synthetic eval is not required if sufficient shadow matches accumulate across multiple sessions.
- **trusted / gate-capable** rules require autonomous posthoc/shadow evidence; `nokori edit --status` is intentionally rejected.

---

## Runtime evidence and posthoc

The hot path compiles trigger data, checks required concepts/exclusions, applies dynamic IDF trigger evidence, records complete fire events, and enqueues posthoc evaluation after session end.

---

## Project ID

Nokori finds the project root with `git rev-parse --show-toplevel` and builds `<dirname>-<first 8 chars of path hash>` as the project_id. A non-git directory falls back to cwd, same format.

### Project / global scope

- `project_scope=project`: this project + global rules
- `project_scope=global`: eligible everywhere once the lifecycle lets it into the formal pool

Scope is not a shortcut around trust.

---

## Maintenance tasks

Maintenance runs from `SessionStart` on configured intervals:

| Task | Interval | Description |
|------|----------|-------------|
| Lifecycle transitions | Daily | posthoc/shadow evidence updates states |
| Candidate cleanup | At most every 30 days | Archive 20-day normal candidates, 40-day anti_pattern |
| Replacement recovery check | At most every 90 days | Restore archived replacement targets if missing |
| Session file cleanup | — | Delete registry files ended 60+ days ago |
| Hook coalesce cleanup | — | Delete claim files older than 24 hours |
| Prompt ack cleanup | — | Delete ack/deferred files older than 24 hours |
| Fire event cleanup | At most every 7 days | Delete fire events older than 30 days |

Run a pass immediately:

```bash
nokori maintain
```

---

## Database

Every rule lives in one SQLite file, `rules.db`, created automatically on first use. After switching machines or upgrading, if it won't open, `nokori export` a backup first.
