# Nokori (残り)

**Languages:** [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

> What experience leaves behind runs deeper than memory.

**A rule notebook for Claude Code** — turns your corrections and hard-won lessons into rules that are automatically recalled next time.

It does not record “what you talked about last time”; it records “what to do next time”: remind Claude in similar situations, and when needed **block one tool call** so it reads the rule before changing code.

---

## Who is it for?

- People who keep correcting the same kinds of mistakes (force push, forgotten migrations, dangerous commands)
- People who want **cross-project** “don’t do that” knowledge instead of starting over in every repo
- People who accept “rules live in local SQLite and can be exported,” and do not want to resend entire chats to an LLM

---

## One minute overview

```
You correct Claude
    → Nokori records a rule (trigger scenario + what to do)
    → Next time your message resembles that moment
    → Automatically written into Claude’s context (reminder)
    → For high-risk correction types with a strong match: block once before the first file edit or command (Gate)
```

**During chat**, Nokori stays fast (retrieval + files, no LLM); **after the session ends**, an LLM mines new rules from the transcript (session log).

---

## Glossary

If you hit English abbreviations on first read, skim this table first; key concepts are repeated later.

| Term | Meaning |
|------|---------|
| **hook** | A small command Claude Code runs automatically at fixed moments (e.g. before/after each message) |
| **injection** | Writing matched rules into the context Claude sees for the current turn |
| **Gate** | For a few “high-risk correction” rules: **deny** the first matching tool call once, forcing Claude to read the rule |
| **marker** | A temporary “read Gate rules first” flag for the current turn; cleared after one use |
| **transcript** | Claude’s full-session `.jsonl` log; read when extracting rules automatically |
| **trigger / action** | The two halves of a rule: “under what situation” + “what to do” |
| **short_id** | A rule’s short ID (e.g. `a3f2b1`), used to dismiss or cross-reference |
| **dismiss** | Retire a rule (no longer retrieved, no longer gated) |
| **HOT / WARM** | Match tiers: highly relevant / somewhat relevant; hotter tiers get more text |
| **BM25** | Keyword-overlap scoring; zero GPU, enabled by default |
| **embedding** | Semantic similarity scoring; optional once you have enough rules |
| **RRF** | Algorithm that merges BM25 and vector rankings into one list |
| **fail-open** | When Nokori itself errors, it **does not block** Claude — it skips reminders for that turn |
| **extract** | Use an LLM to **extract** candidate rules from a transcript (cold path, not urgent) |
| **shadow pool** | Rules from other projects: used only to decide whether to promote to global; **not injected into your current chat** |
| **promotion** | After a project rule is validated across multiple other projects, it becomes **global** (visible everywhere) |
| **candidate / active / dormant** | Pending confirmation → in use → dormant after long disuse |
| **merged / archived** | Superseded by a newer rule / dismissed by you or the system |
| **supersede** | A new rule replaces an old one (old status becomes `merged`) |
| **OpenAI-compatible** | Point the API at `.../v1` to use Ollama, LM Studio, OpenRouter, etc. |

---

## How it works

Nokori registers **4 hooks** in Claude Code. During normal chat they only query the local DB, score, and read/write small files — **no LLM calls in hooks** (otherwise every message would wait on a model).

| Hook | In plain terms | Latency budget |
|------|----------------|----------------|
| `SessionStart` | Session start: optionally inject unextracted user snippets from the previous session + trigger DB maintenance | ≤ 1.5s |
| `UserPromptSubmit` | Each message sent: retrieve rules → inject context → write Gate marker if needed | ≤ 500ms |
| `PreToolUse` | Before a tool call: if a marker exists, **block once**, then clear the marker | ≤ 50ms |
| `SessionEnd` | Session close: write a pending extract job; in async mode may run extract in the background | ≤ 200ms |

Two core behaviors:

1. **Reminder (injection)** — matched rules are written into `additionalContext` by HOT/WARM tier so Claude sees them before replying
2. **Block once (Gate)** — only **correction / anti_pattern** rules that match accurately, with high confidence, and are **active** block tools; **solution** rules remind only, never block (see [Injection vs blocking](#injection-vs-blocking))

---

## Installation

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
pip install -e .

# Optional: local embedding (installs sentence-transformers and downloads model weights to ~/.nokori/models/)
pip install -e ".[local-embed]"

# Register hooks (default: Claude Code only; with [local-embed], also prefetches weights)
nokori install              # same as --claude → ~/.claude/settings.json
nokori install --cursor     # native Cursor → ~/.cursor/hooks.json
nokori install --claude --cursor   # both (prints duplicate-hook warning)
# Skip weight download: nokori install --no-prefetch-embed
# Manual download/retry: nokori embed prefetch

# Verify
nokori health
nokori status
nokori logs          # hook / pipeline / async-extract logs
```

`nokori install` writes the hooks above into `~/.claude/settings.json`, **merging** with your existing plugins rather than overwriting them. If `settings.json` is corrupted (invalid JSON), install **refuses to write** and exits (same validation as `nokori health` for settings).

```bash
# Preview changes before writing
nokori install --dry-run

# Uninstall (removes only nokori hooks, keeps others)
nokori install --uninstall

# Temporarily disable (hooks remain but do not run)
nokori install --disable
nokori install --enable
```

### Using Nokori in Cursor

Pick **one** registration path (do not combine):

| Mode | Command / setup |
|------|-----------------|
| **A. Claude import (recommended)** | `nokori install` or `--claude`, then Cursor **Settings → Hooks → Import from Claude Code** |
| **B. Native Cursor** | `nokori install --cursor` → `~/.cursor/hooks.json`; do **not** enable Claude import |

**Avoid duplicate runs**

- Mode A: turn off **project-level** hooks imported from this repo’s `.claude`; keep user-level `~/.claude` nokori only.
- Mode B: use `--cursor` only; no Claude import.
- **`nokori install --claude --cursor`** prints a warning (also on `--dry-run`); single-target installs do **not**.

See `nokori install --help`.

**Gate and Cursor tool names**: Cursor Agent uses `Shell` for terminal commands (Claude Code uses `Bash`). If the **first-layer** PreToolUse `matcher` in `settings.json` only lists `Bash`, `Shell` never invokes the hook. Extend nokori’s `PreToolUse.matcher` to include `Shell`, or use `*`. When the transcript path is under `~/.cursor/...`, Nokori’s **second-layer** `[gate]` matcher defaults include `Shell` (see [Gate two-layer matching](#gate-and-pretooluse-two-layers-of-tool-matching)).

**Cursor prompt injection (official limits)**: Per [Cursor hooks docs](https://cursor.com/docs/agent/hooks), `beforeSubmitPrompt` output is only `continue` and `user_message` — not Claude’s `additionalContext`. Nokori still runs retrieve + gate on every send; **gate blocking** uses Cursor-native `permission: deny` on `preToolUse`. **Hot-cache** text at session open uses `sessionStart` → `additional_context`. Per-prompt rule text on Cursor is sent as best-effort `additional_context` on `beforeSubmitPrompt` (may be ignored by Cursor); for reliable injection, prefer Claude Code or project rules.

**Deferred injection when `beforeSubmitPrompt` is skipped**: If Cursor does not run `beforeSubmitPrompt` for a user turn, Nokori may **deny the first matching tool** (`Shell`, `Write`, etc.) on `preToolUse`, inject the full rule text via `agent_message`, and mark that turn as handled. **Run the tool again** after the deny — this is intentional, not a bug. Later tools in the same turn are not denied again (dedup by `generation_id` + prompt hash, or prompt hash alone when `generation_id` is absent).

---

## Quick start

These three steps are enough to feel Nokori; details are in later sections.

### 1. Add a rule manually

```bash
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --rationale "force push overwrites peers' work" \
  --source-type correction \
  --confidence high \
  --variants "git push --force,git push -f" \
  --terms-zh "强推,覆盖代码"
```

Without `--project-id`, the rule is written with `project_scope=global` (visible in the formal pool for all projects). With `--project-id`, `project_scope=project` and bound to that `project_id`.

### 2. Simulate retrieval (no Claude session required)

```bash
nokori test "I'll just git push --force this branch"
# Default project_id = current directory git root (same as hooks); override with --project
```

Output:

```
prompt        "I'll just git push --force this branch"
candidates    1 rules in pool
bm25.matches  1

HOT  (1):
  abc123  rrf=0.0164  bm25=1.53  matched=['branch', 'force', 'git', 'push']
    Force pushing to a shared branch
WARM (0):

gate.would_block  True
  abc123: Use --force-with-lease, or push to a new branch
```

### 3. Try it in a real session

Open Claude Code and work as usual. When your message resembles a rule:

- Claude **sees injected rules before replying** (HOT gets more text, WARM gets a short line)
- For **correction / anti_pattern** with a very strong match: the first Write / Bash / etc. may be **blocked once**; the UI shows the reason and `short_id`
- **Within the same message**, after one block, later tool calls proceed (marker cleared)
- **Solution** rules may appear in prompts but **never block tools**

### 4. Outdated rules? (Dismiss)

Each rule has a **short_id** (e.g. `a3f2b1`), shown in injection text and Gate block reasons. When a rule no longer applies, **retire** it (status becomes `archived`; no retrieval, no Gate).

**Option 1: terminal (always available)**

```bash
nokori dismiss a3f2b1
```

**Option 2: say it in chat (works with Gate / injection hints)**

When a rule was just injected, or Claude was blocked by Gate, the hint says you can write `dismiss <short_id>` to retire it. In your **next user message**:

```text
dismiss a3f2b1
```

The `UserPromptSubmit` hook recognizes this and archives the rule.

| Comparison | CLI `nokori dismiss` | Chat `dismiss <short_id>` |
|------------|----------------------|---------------------------|
| Time window | Injected within the **past 24 hours** (any session) | Injected within **past 24 hours**; normal `session_id` limits to current session; when `session_id` is `-`, same as CLI (any session) |
| Verb | Fixed subcommand | Configurable via `dismiss_phrase` (default `dismiss`) |

If you set `dismiss_phrase` to `forget`, write `forget a3f2b1` in chat (`nokori dismiss` subcommand name unchanged). Format is fixed: **one word + space + short_id**, not free-form natural language.

Configuration: `dismiss_phrase` / `NOKORI_DISMISS_PHRASE`, see [Configuration file](#configuration-file) and [config.toml.example](config.toml.example).

---

## Gate and PreToolUse: two layers of tool matching

> **What is Gate?** It does not disable tools for the whole session. On the **first** sensitive tool call in a turn, Claude must see the relevant rule first. After one block the marker is cleared; later tool calls in the same message run normally.

Many people assume there is a single “Gate blocks tools” switch. There are actually **two layers**, configured in different places:

```
Claude is about to call a tool
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Claude Code settings.json PreToolUse.matcher   │
│ “Should the nokori hook pre-tool-use run?”              │
│ Default: Edit|Write|MultiEdit|Bash|NotebookEdit         │
│ Read / Grep etc. do not enter the hook by default       │
└─────────────────────────────────────────────────────────┘
    │ hook ran
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Nokori [gate].matcher (NOKORI_GATE_MATCHER)    │
│ “Inside the hook, should this tool_name be blocked?”    │
│ Default: same as above; Python regex, fullmatch on      │
│ payload.tool_name                                       │
└─────────────────────────────────────────────────────────┘
    │ marker present and matched
    ▼
  deny once → delete marker → retry same tool → allowed
```

When Gate blocks, the hook returns Claude Code’s official format ([Hooks reference — PreToolUse](https://code.claude.com/docs/en/hooks)): `hookSpecificOutput.permissionDecision: "deny"` and `permissionDecisionReason` (shown to Claude). Top-level `decision`/`reason` for that event are deprecated; Nokori no longer emits them.

### Layer 1: which tools run the hook

- **Config file**: `~/.claude/settings.json` (written by `nokori install`; does not read `config.toml`)
- **Field**: `matcher` on the nokori entry under `hooks.PreToolUse`
- **Default** (on install): `Edit|Write|MultiEdit|Bash|NotebookEdit`
- **Run hook on any tool**: set that entry’s `matcher` to `*` (Claude Code convention for all PreToolUse events)

Example (nokori entry only; keep your other hooks):

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

If already installed, **edit settings manually**, or `nokori install --uninstall` then `install` (writes repo defaults, not `*`). No `config.toml` change needed afterward.

### Layer 2: which tool_name values actually block

- **Config file**: `[gate] matcher` in `~/.nokori/config.toml`, or env var `NOKORI_GATE_MATCHER`
- **Meaning**: when the hook already ran, match `tool_name` in the payload with Python `re.fullmatch`
- **Default**: `Edit|Write|MultiEdit|Bash|NotebookEdit`
- **Block any tool that entered the hook**: set to `.*` (**not** literal `*`, which is invalid in regex)

```toml
[gate]
matcher = ".*"
```

Changing only this layer while settings still exclude Read: Read **never enters the hook**, so it cannot be blocked. Change **both layers** for “any tool may be gated.”

### Injection vs blocking

| | Injection (`additionalContext`) | Gate (PreToolUse deny) |
|--|--------------------------------|-------------------------|
| Rule scope | Formal pool HOT + WARM | Subset of formal pool HOT |
| `source_type` | All (including solution, preference) | **correction**, **anti_pattern** only |
| Other conditions | Retrieval tier thresholds met | Also **high** + **active** |

Example: a `solution` rule can appear in HOT prompts but **will not** Gate-block your first Write/Bash.

### Other Gate-related settings

| Setting | Purpose |
|---------|---------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | Master switch; off = inject only, no block |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | Marker TTL (default 600s); expired markers do not block; **`0` = never expire** |

**Prompt-hash mismatch (fail-open)**: `UserPromptSubmit` stores the current prompt hash when writing a marker; `PreToolUse` resolves the current hash from the payload or the latest `injections.prompt_hash` for this session (**not** the newest marker file on disk). If unresolved or mismatched (user already sent the next message), **delete the marker and allow the tool**, no block.

---

## Automatic extraction

Background work after a session ends: with LLM configured, Nokori reads Claude Code’s **transcript** (`.jsonl` session log), summarizes corrections into candidate rules, then merges with existing rules.

```bash
# Configure LLM (any OpenAI-compatible endpoint)
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# Manual extract (specify transcript; project prefers project_id from SessionEnd job)
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# Or dry-run preview
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# Consume all pending extract jobs
nokori extract
```

Pipeline: read transcript (single file ≤ 50MB) → compress (keep user messages, truncate AI replies) → LLM extract candidates → merge with existing rules (SAME/BROADER/CONTRADICTS/UNRELATED).

**LLM call shape**: extract and merge use **system** (fixed instructions) + **user** (untrusted body); transcript / candidates / existing rule text are wrapped in `--- BEGIN UNTRUSTED DATA ---` blocks to reduce prompt-injection from tool output. Remote endpoints use OpenAI-compatible `/v1/chat/completions`; when unconfigured, fallback is `claude -p` (system via `--system-prompt`, body on stdin).

**Merge decisions (implementation)** — LLM relation letters `A`–`E` map to SAME / BROADER / NARROWER / CONTRADICTS / UNRELATED:

| Decision | Behavior |
|----------|----------|
| **SAME (A)** + existing `candidate` | Add evidence; high correction may activate immediately, else activate per evidence rules |
| **SAME (A)** + existing `active` / `dormant` | **No new rule**; `add_evidence(..., "same_extraction", 1)` on existing row, full history kept |
| **BROADER / CONTRADICTS (B/D)** | Insert new rule and `supersede` old; if same round already has **A**, `supersede` to A’s rule, no second active insert |
| **NARROWER (C)** | Insert new rule (coexists with existing); if same round also has **SAME (A)**, still insert this candidate |
| **UNRELATED (E)** | Insert new `candidate`, independent of neighbors |
| No strong relation | Insert new `candidate` |

**Merge LLM failure** (neighbors exist but relation JSON invalid/timeout): **current candidate** still inserted as standalone rule, but `merge_ok=false`; `nokori extract` **does not** mark transcript extracted; job **stays pending** (checkpoint keeps processed candidates) for retry.

**Extract LLM failure** (or non-JSON): **no candidates inserted**; job **stays pending**.

**Neighbor backfill (intentional in v0.1)**: when BM25 pre-filter yields fewer than 5 rules, recent rules by `updated_at` are added for the LLM, costing extra tokens and many UNRELATED hits — reduces missed merges with zero token overlap; no toggle. Tradeoff: prefer extra LLM calls over missing SAME/B/D merges.

Without LLM config, Nokori tries `claude -p --model haiku` as fallback (prompt on stdin, not argv).

---

## Database

- SQLite `rules.db`, created automatically on first use
- If the database is incompatible with the current nokori version, operations error; run `nokori export` first, or use a fresh `NOKORI_DATA_DIR` / `nokori reset`

## Rule lifecycle

> Status names are English; meanings are in [Glossary](#glossary). This section is for fine-tuning.

```
candidate → active → dormant → may reactivate or archived
              ↘ merged (superseded by newer rule)
```

| Status | Injected? | Gated? | How it gets here |
|--------|-----------|--------|------------------|
| `candidate` | No | No | Auto-extract, moderate confidence, observe first |
| `active` | Yes | Maybe, if HOT and type matches | Manual high correction, or enough evidence |
| `dormant` | Yes, but at most WARM when hit | No | 30 days without a “strong” hit (see `last_hit`) |
| `merged` | No | No | Superseded by newer rule |
| `archived` | No | No | You dismiss, or candidate cleaned up |

### Activation conditions

- **Manual `nokori add`** or **extract merge**: `high` + `correction` candidate → directly `active` (includes initial `user_correction` evidence)
- Pure AI evidence (including cross-project `shadow_hot`): `evidence_score >= 2` across `>= 2` active days

**`last_hit` semantics**: used for dormant scan (`created_at` if `last_hit` missing). Updated when: **(1)** formal pool HOT/WARM **actually written to context**; **(2)** dormant rule hits retrieval threshold and reactivates this turn. `hit_count` still increments only on HOT injection.

**Dormant reactivation**: when retrieval score reaches HOT tier, **this turn** still injects as WARM (no gate); DB **this turn** sets `status=active` and updates `last_hit`; **next turn** may HOT + gate (if correction/anti_pattern). Matches `UserPromptSubmit` hook behavior.

### Project ID

Nokori resolves the project root via `git rev-parse --show-toplevel` and builds `<dirname>-<first 8 chars of path hash>` as `project_id`. Same repo name at different paths does not collide. Non-git directories fall back to cwd path hash.

### Global Promotion

Each `UserPromptSubmit` searches **formal pool ∪ shadow pool** once (BM25 + optional embedding RRF), then splits by pool: only formal pool HOT/WARM inject; shadow pool **HOT and WARM** both call `record_shadow_hit` (promotion only, not injected into current chat). After **≥3 distinct project_id** hits, rule becomes `global` (**no second confirmation**, v0.1 product choice). `preference` rules do not participate.

### Shadow Pool

**Summary**: while coding in project A, validated rules from project B still **score**, but **are not injected into A’s chat** — only used to decide whether the rule should go global.

- Same retrieval as current-project rules (BM25; embedding + RRF when enough rules)
- **HOT or WARM** both record a shadow hit (promotion evidence)
- **At most 1 hit per other project per calendar day** (same project same day does not stack)
- **≥3 distinct projects** hit → rule becomes `global`, no manual confirm

On a new project with zero rules, shadow pool still runs if promotion is on — builds cross-project consensus from scratch. Disable: `NOKORI_PROMOTION_ENABLED=0`.

Progress: `nokori status` shows `shadow_hits` and `N/3 projects=...`.

### Async Extract Mode (auto-extract after session close)

```bash
export NOKORI_EXTRACT_MODE=async
```

- **`manual` (default)**: session end writes a pending job; you run `nokori extract` yourself
- **`async`**: session end tries to run extract in the background (if a process is already running, queue only, no duplicate spawn)

Logs: `~/.nokori/logs/async-extract.log`. Without LLM config, tries local `claude -p` fallback.

If `{data_dir}/extract.lock` is held (another extract instance, or stale lock), SessionEnd **does not** auto-spawn; pending job remains — run `nokori extract` manually later.

If the transcript is still appended after SessionEnd (file `mtime` changes), `nokori extract` **refreshes job mtime and keeps pending**, does not silently drop the job.

Corrupt `extract-*.json` (unparseable) moves to `{data_dir}/jobs/bad/` during `list_jobs` / `nokori extract` / SessionStart maintenance, avoiding zombie jobs.

Optional: `NOKORI_EXTRACT_DEFER_ACTIVE=1` — in async mode, if **other sessions without SessionEnd** remain (`active_sessions/` with empty `ended_at`, `count_open_sessions`), current SessionEnd **writes job only, no fork** of `nokori extract`; extract after other sessions end or via manual/next SessionEnd trigger.

`NOKORI_SESSION_IDLE_SECONDS` (`[session] idle_seconds`) **does not** participate in defer; only used for “active” display in `nokori status` (open + recent `touch` heartbeat).

Extract jobs are consumed by `nokori extract` (manual or async child). In **`async` mode**, SessionStart **retries** background extract spawn when a pending job exists and extract lock is free. `nokori extract` uses `{data_dir}/extract.lock` (Unix / Windows) to prevent concurrent processing; if another instance runs, **exit 2** with `(extract already running)` (distinct from exit 0 for no pending job).

### Hot cache

SessionStart finds “previous session transcript”:

1. **Prefer** `{data_dir}/transcript_index/` (previous/current pointers written by SessionEnd) — the **last session that ended normally in that directory**, not necessarily the oldest `*.jsonl` by mtime.
2. **Fallback**: latest `*.jsonl` in the same directory with mtime strictly before the current file (heuristic, scan at most 50 files).

If the previous session was not extracted yet, read the last 3 user messages from the file tail for injection (500 chars, separate budget). **Dormant pseudo-HOT, shadow counts, HOT `hit_count`** are all written in **UserPromptSubmit this turn**, not deferred to next SessionStart.

**Shadow and candidate activation**: cross-project shadow HOT calls `add_evidence(..., shadow_hot, 1)`. If another project’s rule is still `candidate`, repeated shadow hits on different days may satisfy pure AI activation (score≥2 and 2 active days) — **unlike “promotion only” intuition, v0.1 intentionally allows** cross-project retrieval evidence to activate.

### Maintenance

Maintenance runs automatically on `SessionStart` (interval checks):

- **Dormant scan** (every 7 days): active with no hit for 30 days → dormant
- **Candidate cleanup** (scan interval at most every 30 days): delete ordinary candidates with **created_at ≥20 calendar days**, `anti_pattern` candidates **≥40 days** (not “alive 30 days”)
- **Unmerge check** (at most every 90 days): `status=merged` rules whose `superseded_by` target was deleted or is dormant/archived revert to `dormant`; **immediate orphan unmerge** after candidate cleanup deletes anchor rule
- **Session file cleanup**: delete registry files in `active_sessions/` ended more than 60 days ago
- **Injection cleanup** (scan interval at most every 7 days): delete `injections` rows **older than 30 days** (dismiss only checks 24h; buffer retained)

Manual trigger:

```bash
nokori maintain
```

---

## Retrieval engine

> **How are relevant rules found?** Keywords first (BM25), semantic vectors when you have enough rules, then RRF merges both lists. HOT/WARM tiers control how much text goes into context.

### BM25 (default, zero dependencies)

- Document fields: `trigger_text`, `trigger_variants`, `search_terms`, **`action`**
- Latin text: lowercase word tokens (≥ 2 chars)
- CJK text: primarily bigrams; single CJK characters keep unigrams (higher recall)
- Mixed text switches automatically

### Embedding (optional)

When rules **≥ 20** (in the batch searched for this prompt) and a remote API is configured or `pip install nokori[local-embed]` is installed, semantic retrieval is added automatically.
`NOKORI_EMBED_ENABLED=1` forces an attempt (small pools may still use BM25 only on first pass; see below).

**Two thresholds (easy to confuse)**:

| Scenario | Count scope | Purpose |
|----------|-------------|---------|
| **SessionStart** `embed` kickstart | All `active+dormant` in DB | Whether to spawn embed server in background (≥20 may spawn, regardless of how few rules the current project has) |
| **UserPromptSubmit** retrieval | Formal∪shadow pool size for this prompt | Whether this prompt uses embedding RRF |

**Partial index**: with embed enabled, rules **without** a `rule_embeddings` row rely on BM25 only in RRF (just activated, post-import, or failed index). Semantic search uses only `rule_embeddings` rows matching the **current configured embed model name**; after model or dimension change, `reindex` / re-`add` or `import`. `nokori health` `embed.index` warns on missing rows; remote probe counts ok only on **HTTP 2xx** (401/404 not healthy).

Remote API mode:

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
# NOKORI_EMBED_DIMENSIONS defaults to unset (model’s own dims); set only for OpenAI text-embedding-3 etc.
```

Local model mode (no URL config):

```bash
pip install nokori[local-embed]
# Or dev install: pip install -e ".[local-embed]"
```

Installing `[local-embed]` pulls Python deps; **model weights** (`paraphrase-multilingual-MiniLM-L12-v2`, ~118MB, 384-dim) download to `~/.nokori/models/` at these times (not in hooks — avoids timeout):

| When | Notes |
|------|-------|
| `pip install …[local-embed]` | Auto prefetch after install (`pip install -e` too) |
| `nokori install` | Prefetches if `[local-embed]` installed, **regardless of hook registration** |
| `nokori embed prefetch` | Manual download or retry |

With no remote embed endpoint and ≥20 retrievable rules, the **embed shared process** loads the model from that directory.

Hook behavior (`NOKORI_EMBED_SERVER_AUTO_START=1`, default on):

- **SessionStart**: if local weights exist in cache → non-blocking `spawn` embed server; **missing weights log only**, no block, no `import sentence_transformers` in hook
- **UserPromptSubmit**: if server not `ping`-able → background spawn, **this turn BM25 only**; RRF usually from next turn
- No model download or long load inside hooks (avoids Claude hook timeout)

`nokori embed start` can warm up early; `NOKORI_EMBED_ENABLED=1` forces embed attempt (even if rules <20); first message on tiny pools may still be BM25-only.

Priority: remote API (base_url set) > local embed server (`[local-embed]` installed) > BM25 only. If server not ready, fall back to BM25; do not load the model in every hook subprocess.

Both scores merge via **RRF**, then HOT/WARM tiers apply.

**Platform note**: local embed is **macOS / Linux only** (`embed.sock`). Windows: BM25 only or remote `NOKORI_EMBED_BASE_URL`.

Local embed management (Unix):

```bash
nokori embed prefetch # Download local model weights (skip if pip/install already did)
nokori embed start    # Background shared server (hooks also auto-start on demand)
nokori embed status   # Process / socket / idle config
nokori embed stop     # Graceful shutdown (SIGTERM + IPC shutdown)
# nokori embed serve  # Foreground debug; exits after NOKORI_EMBED_SERVER_IDLE seconds idle
```

Local embed server Unix socket lives under `NOKORI_DATA_DIR`, **no IPC auth** (acceptable for single-user local use; do not put the data dir on a shared multi-user path).

### Injection tiers

| Tier | Condition | Injected content |
|------|-----------|------------------|
| HOT | top-1 and clearly above top-2 + minimum evidence; **only 1 hit** also needs `rrf_score > 0.01` and ≥3 matched tokens | trigger + action + rationale |
| WARM | others in top-5 (with minimum evidence) | trigger + action one line |
| COLD | outside top-5 | not injected |

**Minimum evidence**: ≥2 query token overlap; or 1 token + trigger variant hit; or embedding cosine ≥ 0.55. Pure embedding hits may have empty `matched_tokens` (still pass cosine threshold for HOT/WARM).

Injection budget: 1500 chars (rules) + 500 chars (hot cache, separate). Only rules **actually written to context** are logged in `injections` and update `last_hit` / HOT `hit_count` (truncated-by-budget rules are not).

---

## Full CLI reference

```bash
# Rule management
nokori add [--trigger "..." --action "..." --source-type ... --confidence ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]

# Extraction
nokori extract [--session <path>] [--dry-run]

# Debugging
nokori test "<prompt>" [--project <id>]
nokori status          # Includes promotion progress: per project rule N/3 distinct projects shadow HOT
nokori logs
nokori health

# Maintenance
nokori maintain
nokori reset [--force]   # Non-interactive terminals require --force

# Local embed shared process (Unix; optional)
nokori embed prefetch | start | stop | status

# Import / export (JSON version field = rules.db schema, currently 2)
nokori export <path.json>
nokori import <path.json>

# Installation
nokori install [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NOKORI_DATA_DIR` | `~/.nokori` | Data root directory |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | Injection character limit |
| `NOKORI_GATE_ENABLED` | `1` | Enable Gate |
| `NOKORI_GATE_TTL_SECONDS` | `600` | Marker expiry; `0` = never expire |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | **Layer 2**: regex for `tool_name` blocked inside hook (use `.*` for any tool); see [Gate two-layer matching](#gate-and-pretooluse-two-layers-of-tool-matching) |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` = defer async extract fork when other sessions active |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | No heartbeat in `active_sessions` beyond this → inactive |
| `NOKORI_HOT_CACHE` | `1` | SessionStart hot cache |
| `NOKORI_PROMOTION_ENABLED` | `1` | Shadow pool and cross-project promotion; `0` disables scenario C |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | Hook remote embed timeout (seconds) |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | Local embed process idle exit (seconds) |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | Hooks auto-start embed server on demand |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions endpoint |
| `NOKORI_LLM_MODEL` | — | LLM model name |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_EMBED_ENABLED` | `0` (auto when active+dormant≥20) | Force embedding on |
| `NOKORI_EMBED_BASE_URL` | — | OpenAI-compatible embeddings endpoint |
| `NOKORI_EMBED_MODEL` | — | Embedding model name |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0` (omit, use model default) | Vector dimensions (only for models that support the parameter) |
| `NOKORI_EMBED_CHUNK_SIZE` | `512` | Text chunk size in characters |
| `NOKORI_EMBED_CHUNK_COUNT` | `3` | Max chunks per rule |
| `NOKORI_STRICT` | `0` | `1` = hook errors propagate (debug; default fail-open) |
| `NOKORI_DISABLED` | `0` | Disable entirely |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | Chat verb to retire rules (`verb + short_id`); see [Dismiss](#4-outdated-rules-dismiss) |
| `NOKORI_LOG_LEVEL` | `warn` | Log level (`debug` also enables `[diag]` hook traces) |
| `NOKORI_HOOK_DEBUG` | `0` | `1` = verbose per-hook `[diag]` lines in `hook.log` |

**Environment variables only** (no `config.toml` field; see [config.toml.example](config.toml.example)):

| Variable | Default | Description |
|----------|---------|-------------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | Directory for `settings.json` read/written by `nokori install` |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | Extra allowed transcript roots, `os.pathsep`-separated (path safety checks) |
| `NOKORI_EXTRACTING` | — | Internal: prevents recursion in `claude -p` fallback child; do not set in user shell or async extract |

All LLM/embedding endpoints are compatible with Ollama, LMStudio, vLLM, OpenRouter, OpenAI, and any `/v1/chat/completions` + `/v1/embeddings` server.

---

## Configuration file

Besides environment variables, Nokori supports TOML at `~/.nokori/config.toml` (path follows `NOKORI_DATA_DIR`).

The repo root has a full template **[config.toml.example](config.toml.example)** (all options, defaults, allowed values, and notes).

**Priority**: environment variables > config.toml > built-in defaults.

```toml
# ~/.nokori/config.toml

log_level = "info"
dismiss_phrase = "dismiss"

[llm]
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-v4-flash"
api_key = "sk-xxx"

[embed]
# Remote OpenAI-compatible API (same [embed] table as server params below — do not duplicate [embed] headers)
base_url = "https://api.example.com/v1"
model = "text-embedding-v4"
api_key = "sk-xxx"
# dimensions = 0  # unset or 0 = do not pass to API (use model default dimensions)
chunk_size = 512
chunk_count = 3
enabled = true
# Local embed shared process (when base_url unset and pip install nokori[local-embed])
# hook_timeout_seconds = 2
# server_idle_seconds = 3600
# server_auto_start = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
# defer_when_active = false   # Defer async extract when other open sessions exist

[hot_cache]
enabled = true

[promotion]
enabled = true

[session]
# idle_seconds = 1800
```

Every field maps to an environment variable (see quick reference in [config.toml.example](config.toml.example)). Missing file is ignored silently; env-only mode works fine.

**Note**: `[gate] matcher` only affects whether Nokori **blocks inside** the hook; whether PreToolUse **invokes** the hook is controlled by `~/.claude/settings.json`, see [Gate two-layer matching](#gate-and-pretooluse-two-layers-of-tool-matching) above. Full `dismiss_phrase` details in [Dismiss](#4-outdated-rules-dismiss).

---

## Data storage

All data is stored locally under `~/.nokori/`:

```
~/.nokori/
├── config.toml           # Config file (optional; env vars take precedence)
├── rules.db              # SQLite (WAL mode): rules + indexes + metadata
├── jobs/                 # Extract job queue
├── active_sessions/      # Session registry
├── gate_markers/         # Gate markers (by session + prompt_hash)
├── logs/
│   ├── hook.log          # Hook process logs
│   ├── pipeline.log      # Extract/merge logs
│   ├── async-extract.log # async mode child stderr
│   └── embed-server.log  # Local embed server (if enabled)
├── models/               # Local embed weights (pip [local-embed] / install / embed prefetch)
├── embed.sock            # Local embed IPC (Unix)
└── extract.lock          # Extract single-instance lock
```

- Zero network sync, purely local
- Rules contain no source code, only behavioral descriptions
- LLM calls send compressed transcript snippets (not source code)
- Point at local Ollama for fully offline operation
- **Database**: tied to the current nokori version; after upgrade or on a new machine, if the DB will not open, `nokori export` first, or use a fresh `NOKORI_DATA_DIR` / `nokori reset`.

---

## Relationship with existing systems

| System | Relationship |
|--------|--------------|
| CLAUDE.md | Complementary. Nokori does not edit CLAUDE.md; rules are dynamic “when X, do Y” |
| Claude Code auto-memory | No conflict. Memory skews factual; Nokori skews behavioral rules |
| Other memory plugins | Hooks can coexist; avoid stacking too many “stuff context” plugins |

---

## Development

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3.11+ -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/   # Do not use system python -m pytest (may collect 0 tests)
```

Project constraints:
- Zero runtime dependencies (`dependencies = []`)
- Pure Python stdlib + urllib for API calls
- No LLM calls on interactive hot paths (UserPromptSubmit / PreToolUse)
- All hooks wrapped in top-level try/except; failures return pass-through

---

## License

MIT
