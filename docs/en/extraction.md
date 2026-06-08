# Automatic Extraction

[← Back to main README](../../README.md)

---

This runs after a session closes, off the interactive path. With an LLM configured, Nokori reads that session's transcript, extracts possible rules, and sends each candidate through the cold pipeline. It does not block chat while it runs.

```bash
# Configure the LLM (any OpenAI-compatible endpoint)
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# Manually extract a given transcript
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# Dry-run preview
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# Consume all pending extract jobs
nokori extract
```

---

## How a transcript becomes rules

The cold path is deliberately more fussy than the hot path. It prefers multiple judgment rounds over inserting ambiguous rules:

1. **Read** the transcript, single-file cap 50MB
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
