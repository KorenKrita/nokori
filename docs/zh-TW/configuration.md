# 設定

[← 返回主文件](../../README.zh-TW.md)

---

## 設定檔

環境變數之外，Nokori 也讀 TOML 設定檔 `~/.nokori/config.toml`（路徑隨 `NOKORI_DATA_DIR` 走）。倉庫根目錄有一份完整範本 **[config.toml.example](../../config.toml.example)**。

**優先順序**：環境變數 > config.toml > 內建預設值。檔案不存在就靜默忽略。

### 按需求找對應設定

| 我想…… | 改這張表 | 關鍵欄位 |
|--------|---------|---------|
| 配後台提取 / 兜底用的 LLM | `[llm]` | `base_url` `model` `api_key` |
| 接遠端或本地的語義檢索 | `[embed]` | `base_url` `model` `enabled` |
| 調 Gate 攔哪些工具、攔多久 | `[gate]` | `matcher` `ttl_seconds` `enabled` |
| 選關會話後自動提取的時機 | `[extract]` | `mode` `defer_when_active` `fork_cache` |
| 開關 SessionStart 熱快取 | `[hot_cache]` | `enabled` |
| 開關 shadow pool 生命週期證據 | `[promotion]` | `enabled` |
| 調 per-role LLM、max tokens、timeouts | `[models]`、`[models.limits]`、`[models.timeouts]` | 見範本 |
| 改對話裡退役規則的動詞 | 頂層 | `dismiss_phrase` |

### config.toml 範本

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

## 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `NOKORI_DATA_DIR` | `~/.nokori` | 資料根目錄 |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | 注入字元上限 |
| `NOKORI_GATE_ENABLED` | `1` | 啟用 gate |
| `NOKORI_GATE_TTL_SECONDS` | `600` | Marker 過期時間；`0` = 永不過期 |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | hook 內 block 的 `tool_name` 正則 |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` 時 async 模式有活躍 session 則推遲 |
| `NOKORI_EXTRACT_FORK_CACHE` | `0` | `1` 時 Claude Code session 結束 fork 複用 prompt cache 提取 |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | 無心跳超過此秒數視為非活躍 |
| `NOKORI_HOT_CACHE` | `1` | SessionStart 熱快取 |
| `NOKORI_PROMOTION_ENABLED` | `1` | 影子池生命週期證據 |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | hook 遠端 embed 逾時（秒） |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | 本地 embed 行程空閒退出（秒） |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | hook 按需自動拉起 embed server |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions 端點 |
| `NOKORI_LLM_MODEL` | — | LLM 模型名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_MODEL_<ROLE>` | — | Per-role LLM 覆寫 |
| `NOKORI_EMBED_ENABLED` | `0`（≥20 規則自動） | 強制啟用 embedding |
| `NOKORI_EMBED_BASE_URL` | — | Embeddings 端點 |
| `NOKORI_EMBED_MODEL` | — | Embedding 模型名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0` | 向量維度 |
| `NOKORI_EMBED_CHUNK_SIZE` | `4000` | 文本分塊字元數 |
| `NOKORI_EMBED_CHUNK_COUNT` | `2` | 每規則最多分塊數 |
| `NOKORI_STRICT` | `0` | `1` 時 hook 例外向上拋出 |
| `NOKORI_DISABLED` | `0` | 完全停用 |
| `NOKORI_HOOK_COALESCE` | `1` | 雙註冊去重 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | 對話裡退役規則的動詞 |
| `NOKORI_LOG_LEVEL` | `warn` | 日誌層級 |

**僅環境變數**（無 config.toml 欄位）：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | install 讀寫的 settings 目錄 |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | 額外允許讀取 transcript 的根目錄 |
| `NOKORI_EXTRACTING` | — | 內部防遞迴標記 |

所有 LLM/Embedding 端點相容：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任何 `/v1` 端點。

---

## 資料儲存結構

```
~/.nokori/
├── config.toml           # 設定檔（可選）
├── rules.db              # SQLite (WAL mode)
├── jobs/                 # Extract job 佇列
├── active_sessions/      # Session registry
├── gate_markers/         # Gate marker
├── hook_coalesce/        # 雙註冊去重 claim
├── logs/
│   ├── hook.log
│   ├── pipeline.log
│   ├── async-extract.log
│   ├── fork-extract.log
│   └── embed-server.log
├── models/               # 本地 embed 權重
├── embed.sock            # 本地 embed IPC（Unix）
└── extract.lock          # extract 單實例鎖
```
