# Configuration

[← Back to main README](../../README.md)

---

## Configuration file

Beyond environment variables, Nokori also reads a TOML config file at `~/.nokori/config.toml` (the path follows `NOKORI_DATA_DIR`). The repo root has a full template, **[config.toml.example](../../config.toml.example)**, listing every option.

**Priority**: environment variables > config.toml > built-in defaults. A missing file is ignored silently.

### Find the right table for your need

| I want to... | Touch this table | Key fields |
|--------------|------------------|------------|
| Configure the LLM for background extract / fallback | `[llm]` | `base_url` `model` `api_key` |
| Hook up remote or local semantic retrieval | `[embed]` | `base_url` `model` `enabled` |
| Tune which tools Gate blocks, and for how long | `[gate]` | `matcher` `ttl_seconds` `enabled` |
| Choose when auto-extract runs after a session | `[extract]` | `mode` `defer_when_active` `fork_cache` |
| Toggle the SessionStart hot cache | `[hot_cache]` | `enabled` |
| Toggle shadow lifecycle evidence | `[promotion]` | `enabled` |
| Tune per-role LLM models, max tokens, and timeouts | `[models]`, `[models.limits]`, `[models.timeouts]` | see template |
| Change the chat verb for retiring rules | top level | `dismiss_phrase` |

### config.toml template

```toml
# ~/.nokori/config.toml

log_level = "info"
dismiss_phrase = "dismiss"

[llm]
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-v4-flash"
api_key = "sk-xxx"

[embed]
base_url = "https://api.example.com/v1"
model = "text-embedding-v4"
api_key = "sk-xxx"
# dimensions = 0
chunk_size = 4000
chunk_count = 2
enabled = true
# server_idle_seconds = 3600
# server_auto_start = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
# defer_when_active = false
# fork_cache = false

[hot_cache]
enabled = true

[promotion]
enabled = true

[session]
# idle_seconds = 1800

[models]
# extractor = "deepseek-v4-flash"
# admission_judge = "deepseek-v4-flash"
# rule_rewriter = "deepseek-v4-flash"
# final_judge = "deepseek-v4-flash"
# merge_planner = "deepseek-v4-flash"
# synthetic_eval_generator = "deepseek-v4-flash"
# posthoc_evaluator = "deepseek-v4-flash"

[models.limits]
# extractor_max_tokens = 4000
# admission_judge_max_tokens = 2000
# rule_rewriter_max_tokens = 4000
# final_judge_max_tokens = 2000
# merge_planner_max_tokens = 3000
# synthetic_eval_generator_max_tokens = 4000
# posthoc_evaluator_max_tokens = 3000

[models.timeouts]
# extractor_timeout = 60
# admission_judge_timeout = 30
# rule_rewriter_timeout = 60
# final_judge_timeout = 30
# merge_planner_timeout = 45
# synthetic_eval_generator_timeout = 60
# posthoc_evaluator_timeout = 45
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NOKORI_DATA_DIR` | `~/.nokori` | Data root directory |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | Injection character limit |
| `NOKORI_GATE_ENABLED` | `1` | Enable gate |
| `NOKORI_GATE_TTL_SECONDS` | `600` | Marker expiry; `0` = never expire |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | Layer 2 regex for `tool_name` blocked inside the hook |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` = in async mode, defer extract when sessions are active |
| `NOKORI_EXTRACT_FORK_CACHE` | `0` | `1` = fork Claude Code sessions for extraction using prompt cache |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | No heartbeat beyond this many seconds = inactive |
| `NOKORI_HOT_CACHE` | `1` | SessionStart hot cache |
| `NOKORI_PROMOTION_ENABLED` | `1` | Shadow pool lifecycle evidence |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | Hook remote embed timeout (seconds) |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | Local embed process idle exit (seconds) |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | Hooks auto-start the embed server on demand |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions endpoint |
| `NOKORI_LLM_MODEL` | — | LLM model name |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_MODEL_<ROLE>` | — | Per-role LLM override |
| `NOKORI_EMBED_ENABLED` | `0` (auto by pool size) | Force embedding attempts on |
| `NOKORI_EMBED_BASE_URL` | — | Embeddings endpoint |
| `NOKORI_EMBED_MODEL` | — | Embedding model name |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0` | Vector dimensions |
| `NOKORI_EMBED_CHUNK_SIZE` | `4000` | Text chunk size in characters |
| `NOKORI_EMBED_CHUNK_COUNT` | `2` | Max chunks per rule |
| `NOKORI_STRICT` | `0` | `1` = hook errors propagate upward |
| `NOKORI_DISABLED` | `0` | Disable entirely |
| `NOKORI_HOOK_COALESCE` | `1` | Dual-registration dedup |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | Chat verb to retire a rule |
| `NOKORI_LOG_LEVEL` | `warn` | Log level |

**Environment variables only** (no `config.toml` field):

| Variable | Default | Description |
|----------|---------|-------------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | Directory for `settings.json` that `nokori install` reads/writes |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | Extra allowed transcript roots, `os.pathsep`-separated |
| `NOKORI_EXTRACTING` | — | Internal recursion guard; do not set manually |

All LLM/embedding endpoints are compatible with: Ollama, LMStudio, vLLM, OpenRouter, OpenAI, any `/v1` endpoint.

---

## Data storage structure

```
~/.nokori/
├── config.toml           # Config file (optional; env vars take precedence)
├── rules.db              # SQLite (WAL mode): rules + indexes + metadata
├── jobs/                 # Extract job queue
├── active_sessions/      # Session registry
├── gate_markers/         # Gate markers (by session + prompt_hash)
├── hook_coalesce/        # Dedup claims when Claude + Cursor both register
├── logs/
│   ├── hook.log          # Hook process logs
│   ├── pipeline.log      # Extract / merge logs
│   ├── async-extract.log # async mode child stderr
│   ├── fork-extract.log  # fork cache extract stderr
│   └── embed-server.log  # Local embed server (if enabled)
├── models/               # Local embed weights
├── embed.sock            # Local embed IPC (Unix)
└── extract.lock          # Extract single-instance lock
```
