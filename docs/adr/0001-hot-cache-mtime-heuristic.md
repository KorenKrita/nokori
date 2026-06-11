# Hot cache uses mtime heuristic, not session registry

SessionStart hot_cache finds the previous transcript by scanning `*.jsonl` in the same directory for the file with mtime strictly less than the current file. This is simpler than maintaining a session registry or reading `active_sessions/`.

Considered: reading from `active_sessions/` registry or maintaining a session-id chain. Rejected because mtime requires zero schema, zero migration, and works correctly for the default Claude Code layout (transcripts in one projects directory).

Known limitation: parallel sessions, non-adjacent files, or external mtime modification may inject from the wrong "previous" session. Accepted for v0.1; reliable version (registry or payload chain) is a v0.2 candidate. Disable with `NOKORI_HOT_CACHE=0`.
