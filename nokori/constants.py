"""Shared defaults used across CLI, config, and hooks."""

DEFAULT_GATE_MATCHER = "Edit|Write|MultiEdit|Bash|NotebookEdit"

# Extract / hot_cache transcript reads
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024

# claude -p fallback stdin cap (extract cold path)
MAX_CLAUDE_CLI_INPUT_CHARS = 100 * 1024

# Job mtime: treat as unchanged when within float/stat noise only
TRANSCRIPT_MTIME_EPSILON_SEC = 1e-6
