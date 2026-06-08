# Retrieval Engine

[← Back to main README](../../README.md)

---

How does Nokori pick the handful of rules relevant to your prompt from the full library? Three steps: keyword scoring with BM25, semantic vectors once enough rules exist (embedding), then fuse the two rankings with RRF. HOT / WARM tiers decide how much text to include in the context.

---

## BM25 (default, zero dependencies)

Works out of the box, no model or GPU required.

- Indexes these four fields: `trigger_text`, `trigger_variants`, `search_terms`, `action`
- Latin text: lowercased, tokenized, only words of length >= 2 are kept
- CJK: mostly bigrams (adjacent pairs), with single stray CJK characters kept as unigrams to lift recall
- Mixed Chinese/English is handled automatically

---

## Embedding (optional)

Once rules reach **>= 20** and you've either configured a remote API or installed `pip install nokori[local-embed]`, semantic retrieval stacks on automatically. Want to force a try? `NOKORI_EMBED_ENABLED=1`.

Two thresholds both called "20" — they count different sets of rules:

| Scenario | What it counts | What it decides |
|----------|----------------|-----------------|
| **SessionStart** embed kickstart | The whole DB's `active + trusted` total | Whether to spin up an embed server in the background |
| **UserPromptSubmit** retrieval | This pass's `formal ∪ shadow` pool size | Whether this prompt goes through embedding RRF |

### Remote API mode

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
```

### Local model mode

```bash
pip install nokori[local-embed]
```

Installing `[local-embed]` pulls in **sentence-transformers>=3.0**. The prefetched model is [ibm-granite/granite-embedding-97m-multilingual-r2](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2) (97M params / 384-dim, ~220MB).

| Component | Size (approx.) |
|-----------|----------------|
| `model.safetensors` | ~186 MiB |
| `tokenizer.json` + configs | ~24 MiB |
| **Total** | ~210-220MB |

Weight download moments:

| When | Notes |
|------|-------|
| `pip install …[local-embed]` | Auto prefetch after install |
| `nokori install` | Prefetches if `[local-embed]` is installed |
| `nokori embed prefetch` | Manual download or retry |

### Hook embed server behavior

- **SessionStart**: if local weights are already cached, non-blocking spawn embed server
- **UserPromptSubmit**: if the server isn't ping-able, background-spawn it and run BM25-only this turn
- Hooks never wait on model download or long load

Priority: remote API (base_url set) > local embed server (`[local-embed]` installed) > BM25 only.

### Local embed management (Unix)

```bash
nokori embed prefetch   # Download local model weights
nokori embed start      # Bring up the shared server in the background
nokori embed status     # Check process / socket / idle config
nokori embed stop       # Graceful shutdown
```

**Platform**: local embed runs on **macOS / Linux** only (via Unix socket). On Windows use a remote `NOKORI_EMBED_BASE_URL` or BM25-only.

---

## Injection tiers

After retrieval, results go through runtime applicability and a selector. The selector uses utility, diversity (MMR-style overlap penalty), status history, false-positive penalties, and the character budget:

| Tier | Entry condition | Injected content |
|------|-----------------|------------------|
| HOT | Eligible `active`/`trusted` result with positive utility; usually max 1 | trigger + action + rationale |
| WARM | Other eligible results that survive utility decay, diversity, and budget caps | trigger + action, one line |
| COLD | Candidate/suppressed/archived, excluded, or insufficient trigger evidence | not injected |

**Trigger evidence** must come from the rule's trigger structure: strong variant phrase + required concepts, or enough dynamic-IDF trigger information. Action-only, search-term-only, embedding-only, excluded-context, and near-miss matches stay COLD.

Injection budget: rules get 1500 chars, hot cache gets 500 chars (independent, neither crowds the other). Only rules actually written to context are recorded as fire events.
