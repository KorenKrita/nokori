# Automatic Extraction

[← Back to main README](../../README.md)

---

This runs after a session closes, off the interactive path. With an LLM configured, Nokori reads that session's transcript, extracts possible rules, and sends each candidate through the cold pipeline. It does not block chat while it runs. Claude Code, Cursor, and OMP all feed the same extractor. On OMP, the installed TypeScript bridge forwards `session_shutdown`, reads the current session file from OMP's session manager, and passes that local JSONL path into the existing Python dispatcher.

```bash
# Configure the LLM (any OpenAI-compatible endpoint)
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# Manually extract a given transcript
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session ~/.omp/agent/sessions/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# Dry-run preview
nokori extract --session ~/.omp/agent/sessions/.../session.jsonl --dry-run

# Consume all pending extract jobs
nokori extract
```

---

## How a transcript becomes rules

The cold path is deliberately more fussy than the hot path. It prefers multiple judgment rounds over inserting ambiguous rules:

1. **Read** the transcript, single-file cap 50MB

   OMP session logs live under `~/.omp/agent/sessions/**/*.jsonl`; they stay local and go through the same compression and extraction pipeline as Claude Code and Cursor transcripts.
2. **Compress**: user messages kept verbatim, AI replies cut to the first 200 chars + last 100 chars; the whole thing is squeezed under about 30k tokens
3. **Extract**: the extractor role emits structured candidates with concepts, required concept groups, variants, excluded contexts, evidence quotes, and source metadata
4. **Judge / rewrite / judge**: admission and final-judge roles reject weak or over-broad rules; a rewriter may tighten scope
5. **Merge**: the merge planner compares the candidate with nearby rules, then deterministic policy decides the outcome
6. **Validate**: archived fingerprints, matcher compilation, and cold-fast-lane thresholds decide whether the result is stored as `candidate` or `active`

**LLM call format**: every role call splits into system + user messages. Transcript snippets are wrapped in `--- BEGIN UNTRUSTED DATA ---` / `--- END UNTRUSTED DATA ---` delimiters to suppress adversarial instructions.

---

## Merge strategy

The LLM returns one relation letter `A`-`E` per candidate:

| Decision | Behavior |
|----------|----------|
| **SAME (A)** | merge_into_existing / replace / reject |
| **BROADER (B)** | Safety/quality judgment decides |
| **NARROWER (C)** | Insert new rule, coexisting with the existing one |
| **CONTRADICTS (D)** | Conservative keep_both or reject_new |
| **UNRELATED (E)** | Insert a new candidate |

Failure handling:

- **Extract LLM failure**: job stays pending
- **Merge LLM failure**: current candidate skipped, job stays pending

**Neighbor backfill**: when the BM25 pre-filter returns fewer than 5 neighbors, Nokori tops up the list with the most recently updated rules by `updated_at`.

---

## Async extract mode

```bash
export NOKORI_EXTRACT_MODE=async
```

| Mode | Behavior |
|------|----------|
| `manual` (default) | Closing a session only drops a to-do file; extraction is yours to run with `nokori extract` |
| `async` | Closing a session tries to run extract in the background directly |

Logs: `~/.nokori/logs/async-extract.log`. With no LLM configured (`NOKORI_LLM_BASE_URL` unset), async mode falls back to invoking the local `claude -p` CLI if available on `$PATH`.

Edge cases:

- `extract.lock` held: does not auto-spawn, pending job preserved
- Transcript mtime changed: refreshes job mtime, keeps it pending
- Corrupt job file: moved to `jobs/bad/`
- `NOKORI_EXTRACT_DEFER_ACTIVE=1`: defers extract fork while other open sessions exist

---

## Fork cache extraction (Claude Code only)

```bash
export NOKORI_EXTRACT_FORK_CACHE=1
```

When enabled alongside `async` mode, Nokori forks the ended Claude Code session using `claude -r <session-id> --fork-session` instead of reading the transcript file. This reuses the model's prompt cache (5-minute TTL), reducing input token cost by ~90% for long conversations.

**How it works:**

1. Session ends → `session_end` hook detects `Host.CLAUDE`
2. Spawns `fork_runner` as a background process
3. Checks byte offset: if this session was partially extracted before, reads the 3rd-to-last user message as an anchor and tells the model to only extract new content
4. Checks for compression: if `compact_boundary` exists after the last extracted offset, skips fork (compressed context loses detail) and falls back to normal transcript-reading extract
5. Forks session with a role-override prompt that forces extraction behavior
6. Parses JSON output → cold pipeline (admission → rewrite → merge → insert)

**Requirements:**

- `claude` CLI on `$PATH`
- `extract.mode = "async"`
- `extract.fork_cache = true`
- Only fires for Claude Code sessions (Cursor sessions always use normal path)

**Fallback:** If the CLI is missing, session ID is invalid, fork times out (300s), or output is not valid JSON, the normal `nokori extract` async path runs instead.

Logs: `~/.nokori/logs/fork-extract.log`
