"""Shared defaults used across CLI, config, and hooks."""

DEFAULT_GATE_MATCHER = "Edit|Write|MultiEdit|Bash|NotebookEdit"

# Cursor PreToolUse uses Shell/Read/Write/Grep/Task instead of Bash/Edit/MultiEdit.
CURSOR_GATE_MATCHER = "Edit|Write|MultiEdit|Bash|Shell|NotebookEdit|Delete|Grep|Task"

# Pi and OMP tool names are lowercase. Map the default sensitive write/execute
# tools without making read-only tools eligible for Gate by default.
PI_GATE_MATCHER = "bash|edit|write"
OMP_GATE_MATCHER = "bash|edit|write"

# Extract / hot_cache transcript reads
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024

# claude -p fallback stdin cap (extract cold path)
MAX_CLAUDE_CLI_INPUT_CHARS = 100 * 1024

# Extract: max rules per transcript (prompt, parser, eval judge/triage)
MAX_EXTRACT_CANDIDATES = 3

# Job mtime: treat as unchanged when within float/stat noise only
TRANSCRIPT_MTIME_EPSILON_SEC = 1e-6
