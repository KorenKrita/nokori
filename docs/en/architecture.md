# Architecture

[← Back to main README](../../README.md)

---

## Autonomous quality flywheel

Nokori is built as an autonomous quality flywheel: every rule has to earn its way from memory into behavior.

The loop is deliberately split in three:

- **Cold path**: after the session closes, a multi-role LLM pipeline extracts, judges, rewrites, merges, and evaluates rule candidates. Weak rules stay out; broad rules get tightened; unsafe merges are rejected or split.
- **Hot path**: during chat, hooks do deterministic retrieval, matching, scoring, marker I/O, and fail-open handling only. No LLM call sits between your prompt and the agent's reply.
- **Evidence loop**: HOT/WARM injections create fire events; candidate/suppressed shadow matches create counterfactual evidence; maintenance applies lifecycle transitions from evaluated evidence.

What makes that loop useful:

- **Structured triggers**, not loose text: concepts, required concept groups, variants, excluded contexts, tool tags, severity, source origin, runtime policy version, and lineage metadata.
- **Autonomous lifecycle**: `candidate → active → trusted`, with `suppressed` recovery and terminal `archived` outcomes. Manual commands can archive, but cannot fake trust.
- **Conservative Gate**: a one-turn reminder brake for `trusted + gate_eligible` rules with strong runtime evidence. It is not a permission system.
- **Hybrid retrieval**: BM25 is always available; optional remote embeddings or the local Granite multilingual model add semantic recall; RRF and runtime applicability decide HOT/WARM.
- **Local-first operations**: SQLite, hook logs, job queues, gate markers, embedding weights, and web dashboard state all live under `~/.nokori/`. Remote LLM/embedding endpoints are opt-in.
- **Cross-tool inspectability**: Claude Code and Cursor both work, and `nokori test`, `status`, `health`, `logs`, `extract`, `maintain`, plus the Web UI explain why a rule did or did not fire.

The important product promise is restraint: Nokori is allowed to remind early, but it must collect evidence before it becomes authoritative, and it must keep collecting evidence after it starts helping.

---

## Hook timing

Nokori registers **4 hooks** in Claude Code (and Cursor). During normal chat they only query the local DB, score, and read/write small files — **no LLM calls inside hooks** — otherwise every message would block on model latency.

| Hook | What it does | Latency budget |
|------|--------------|----------------|
| `SessionStart` | Session start: optionally inject unextracted user snippets from the previous session, and trigger DB maintenance | ≤ 1.5s |
| `UserPromptSubmit` | Each message sent: retrieve rules → inject context → write a Gate marker if needed | ≤ 500ms |
| `PreToolUse` | Before a tool call: if a marker exists, **block once**, then clear the marker | ≤ 50ms |
| `SessionEnd` | Session close: write a pending extract job; in async mode may run extract in the background | ≤ 200ms |

In practice it comes down to two things:

1. **Reminder (injection)** — matched rules are written into `additionalContext` by HOT/WARM tier, so Claude sees them before it replies
2. **Block once (Gate)** — only `trusted` rules with `severity=gate_eligible`, strong prompt evidence, and passing tool-input evidence will gate tools; ordinary active rules only remind

---

## Injection vs blocking

| | Injection (`additionalContext`) | Gate (PreToolUse deny) |
|--|----------------------------------|-------------------------|
| Rule scope | Formal pool HOT + WARM | A subset of formal pool HOT |
| Status | `active` and `trusted` | `trusted` only |
| Severity | `reminder`, `high_risk`, `gate_eligible` | `gate_eligible` only |
| Other conditions | Required concepts, exclusions, dynamic trigger evidence, selection budget | Plus strong prompt evidence, current runtime policy, prompt-hash match, and tool-input evidence when tool input is inspectable |

Gate is not a permission system. It is a one-turn reminder brake: show the relevant rule, deny once, clear the marker, and let later tool calls in the same message proceed.

---

## Shadow Pool

On every `UserPromptSubmit`, Nokori retrieves the **formal pool** and the **shadow pool** separately so shadow evidence cannot steal HOT/WARM slots from real reminders.

- **Formal pool**: `active` + `trusted`; only this pool can inject
- **Shadow pool**: `candidate` + `suppressed`; never injected, never gated
- Candidate shadow matches become counterfactual evidence for candidate → active
- Suppressed shadow matches become recovery evidence for suppressed → active

---

## Hot cache

SessionStart looks for the "previous transcript" in two steps:

1. **Prefer** the previous/current pointers SessionEnd wrote into `{data_dir}/transcript_index/`
2. **Fallback**: in the same directory, the newest `*.jsonl` whose mtime is strictly before the current file

If the previous session hasn't been extracted yet, it injects the last 3 user messages from the **tail** of the file (500 chars, in a budget separate from the 1500-char rule budget).

---

## Glossary

| Term | Meaning |
|------|---------|
| **hook** | A small command Claude Code / Cursor runs automatically at fixed moments |
| **injection** | Writing matched rules into the context the agent sees for the current turn |
| **Gate** | For `trusted` + `gate_eligible` rules: deny the first matching tool call once |
| **marker** | A temporary "read Gate rules first" flag for the current turn; cleared after one use |
| **transcript** | The full-session `.jsonl` log |
| **trigger / action** | The two halves of a rule: "under what situation" + "what to do" |
| **short_id** | A rule's short ID (e.g. `a3f2b1`) |
| **dismiss** | Retire a rule (no longer retrieved, no longer gated) |
| **HOT / WARM** | Match tiers: highly relevant / somewhat relevant |
| **BM25** | Keyword-overlap scoring; zero GPU, on by default |
| **embedding** | Semantic similarity scoring; optional once you have enough rules |
| **RRF** | Algorithm that merges the BM25 ranking and the vector ranking into one list |
| **fail-open** | When Nokori itself errors, it does not block the agent |
| **extract** | Use an LLM to extract candidate rules from a transcript |
| **shadow pool** | Background-matched candidate/suppressed rules: used as evidence, not injected |
| **OpenAI-compatible** | Point the API at `.../v1` to use Ollama, LM Studio, OpenRouter, etc. |
