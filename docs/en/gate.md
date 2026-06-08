# Gate Mechanism

[← Back to main README](../../README.md)

---

> **What is Gate?** Not disabling tools for the whole session, but "before the first sensitive tool call this turn, let Claude see the relevant rule first." After one block the marker is cleared, and later tool calls in the same message run normally.

---

## Two-layer tool matching

```
Claude is about to call a tool
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Claude Code settings.json PreToolUse.matcher   │
│ "Should nokori hook pre-tool-use run at all?"           │
│ Default: Edit|Write|MultiEdit|Bash|NotebookEdit         │
│ Read / Grep etc. do not enter the hook by default       │
└─────────────────────────────────────────────────────────┘
    │ hook ran
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Nokori [gate].matcher (NOKORI_GATE_MATCHER)    │
│ "Inside the hook, should this tool_name be blocked?"    │
│ Default: same as above; must be a Python regex,         │
│ fullmatch against payload.tool_name                     │
└─────────────────────────────────────────────────────────┘
    │ marker present and matched
    ▼
  deny once → delete marker → retry same tool → allowed
```

When Gate blocks, the hook returns `hookSpecificOutput.permissionDecision: "deny"` and `permissionDecisionReason`.

---

## Layer 1: which tools run the hook

- **Config file**: `~/.claude/settings.json` (written by `nokori install`)
- **Default**: `Edit|Write|MultiEdit|Bash|NotebookEdit`
- **To run the hook on any tool**: set the matcher to `*`

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "nokori hook pre-tool-use",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

---

## Layer 2: which tool_name values actually block

- **Config file**: `[gate] matcher` in `~/.nokori/config.toml`, or env var `NOKORI_GATE_MATCHER`
- **Python `re.fullmatch`** against the payload's `tool_name`
- **Default**: `Edit|Write|MultiEdit|Bash|NotebookEdit`
- **To make any tool eligible for blocking**: set to `.*` (not `*`, which is invalid regex)

```toml
[gate]
matcher = ".*"
```

Both layers must be changed to achieve "any tool may be gated."

---

## Other Gate settings

| Setting | Purpose |
|---------|---------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | Master switch; off = inject only, no block |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | Marker TTL (default 600s); `0` = never expire |

---

## Prompt-hash safety

`UserPromptSubmit` records the current prompt's hash when writing a marker. `PreToolUse` verifies hash consistency — if it doesn't match (the user already sent the next message), the marker is deleted and the tool is allowed, no block.
