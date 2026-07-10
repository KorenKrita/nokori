# Gate Mechanism

[← Back to main README](../../README.md)

---

> **What is Gate?** Not disabling tools for the whole session, but "before the first sensitive tool call this turn, let the agent see the relevant rule first." After one block the marker is cleared, and later tool calls in the same message run normally.

---

## Two-layer tool matching

Gate always has two decisions:

1. **Should this runtime call Nokori before the tool runs?**
2. **If Nokori runs, should this `tool_name` be blocked once?**

Runtime layer:

- **Claude Code**: `~/.claude/settings.json` `PreToolUse.matcher`
- **Cursor**: native pre-tool matcher in `~/.cursor/hooks.json`
- **OMP**: the installed bridge at `~/.omp/agent/extensions/nokori.ts`, triggered on `tool_call`

Nokori layer:

- **Config**: `[gate] matcher` in `~/.nokori/config.toml`, or env var `NOKORI_GATE_MATCHER`
- **Matching**: Python `re.fullmatch` against `payload.tool_name`

When Gate blocks, Claude Code and Cursor return `hookSpecificOutput.permissionDecision: "deny"` plus a reason. OMP returns a tool-call block through the bridge with the same reason.

---

## Layer 1: which tools run the hook

- **Runtime files**: `~/.claude/settings.json` for Claude Code, `~/.cursor/hooks.json` for native Cursor, `~/.omp/agent/extensions/nokori.ts` for OMP
- **Claude Code / Cursor default**: `Edit|Write|MultiEdit|Bash|NotebookEdit`
- **OMP note**: the bridge receives every `tool_call`; OMP emits lower-case names such as `bash`, `edit`, `write`, `grep`, `glob`, and `read`
- **To run the hook on any tool**: set the runtime matcher accordingly; for Claude Code, set the matcher to `*`

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
- **Claude Code / Cursor default**: `Edit|Write|MultiEdit|Bash|NotebookEdit`
- **OMP default**: `bash|edit|write`; read-only tools such as `read`, `grep`, and `glob` remain allowed unless you configure a broader matcher
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
