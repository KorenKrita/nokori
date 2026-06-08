# 配置

[← 返回主文档](../../README.zh-CN.md)

---

## 配置文件

环境变量之外，Nokori 也读 TOML 配置文件 `~/.nokori/config.toml`（路径随 `NOKORI_DATA_DIR` 走）。仓库根目录有一份完整模板 **[config.toml.example](../../config.toml.example)**。

**优先级**：环境变量 > config.toml > 内置默认值。文件不存在就静默忽略。

### 按需求找对应配置

| 我想…… | 改这张表 | 关键字段 |
|--------|---------|---------|
| 配后台提取 / 兜底用的 LLM | `[llm]` | `base_url` `model` `api_key` |
| 接远程或本地的语义检索 | `[embed]` | `base_url` `model` `enabled` |
| 调 Gate 拦哪些工具、拦多久 | `[gate]` | `matcher` `ttl_seconds` `enabled` |
| 选关会话后自动提取的时机 | `[extract]` | `mode` `defer_when_active` |
| 开关 SessionStart 热缓存 | `[hot_cache]` | `enabled` |
| 开关 shadow pool 生命周期证据 | `[promotion]` | `enabled` |
| 调 per-role LLM、max tokens、timeouts | `[models]`、`[models.limits]`、`[models.timeouts]` | 见模板 |
| 改对话里退役规则的动词 | 顶层 | `dismiss_phrase` |

### config.toml 模板

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

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NOKORI_DATA_DIR` | `~/.nokori` | 数据根目录 |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | 注入字符上限 |
| `NOKORI_GATE_ENABLED` | `1` | 启用 gate |
| `NOKORI_GATE_TTL_SECONDS` | `600` | Marker 过期时间；`0` = 永不过期 |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | hook 内 block 的 `tool_name` 正则 |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` 时 async 模式有活跃 session 则推迟 |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | 无心跳超过此秒数视为非活跃 |
| `NOKORI_HOT_CACHE` | `1` | SessionStart 热缓存 |
| `NOKORI_PROMOTION_ENABLED` | `1` | 影子池生命周期证据 |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | hook 远程 embed 超时（秒） |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | 本地 embed 进程空闲退出（秒） |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | hook 按需自动拉起 embed server |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions 端点 |
| `NOKORI_LLM_MODEL` | — | LLM 模型名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_MODEL_<ROLE>` | — | Per-role LLM 覆盖 |
| `NOKORI_EMBED_ENABLED` | `0`（≥20 规则自动） | 强制启用 embedding |
| `NOKORI_EMBED_BASE_URL` | — | Embeddings 端点 |
| `NOKORI_EMBED_MODEL` | — | Embedding 模型名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0` | 向量维度 |
| `NOKORI_EMBED_CHUNK_SIZE` | `4000` | 文本分块字符数 |
| `NOKORI_EMBED_CHUNK_COUNT` | `2` | 每规则最多分块数 |
| `NOKORI_STRICT` | `0` | `1` 时 hook 异常向上抛出 |
| `NOKORI_DISABLED` | `0` | 完全禁用 |
| `NOKORI_HOOK_COALESCE` | `1` | 双注册去重 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | 对话里退役规则的动词 |
| `NOKORI_LOG_LEVEL` | `warn` | 日志级别 |

**仅环境变量**（无 config.toml 字段）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | install 读写的 settings 目录 |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | 额外允许读取 transcript 的根目录 |
| `NOKORI_EXTRACTING` | — | 内部防递归标记 |

所有 LLM/Embedding 端点兼容：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任何 `/v1` 端点。

---

## 数据存储结构

```
~/.nokori/
├── config.toml           # 配置文件（可选）
├── rules.db              # SQLite (WAL mode)
├── jobs/                 # Extract job 队列
├── active_sessions/      # Session registry
├── gate_markers/         # Gate marker
├── hook_coalesce/        # 双注册去重 claim
├── logs/
│   ├── hook.log
│   ├── pipeline.log
│   ├── async-extract.log
│   └── embed-server.log
├── models/               # 本地 embed 权重
├── embed.sock            # 本地 embed IPC（Unix）
└── extract.lock          # extract 单实例锁
```
