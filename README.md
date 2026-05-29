# Nokori (残り)

> 経験留下的痕迹，比記憶更深的東西。

**Claude Code 的反复犯错纠正层。**

从你的纠正和 AI 的踩坑中自动提炼结构化规则，在未来相似场景下注入上下文并强制 Claude 在动手前确认——不是记住发生了什么，而是学会下次怎么做。

---

## 工作原理

```
你纠正 Claude → Nokori 提炼规则 → 下次相似场景 → 注入 + 阻断 → Claude 先看规则再动手
```

Nokori 通过 4 个 Claude Code hooks 运行，交互热路径零 LLM 调用：

| Hook | 作用 | 延迟预算 |
|------|------|----------|
| `SessionStart` | 热缓存注入 + 轻维护（dormant 扫描、candidate 清理） | ≤ 1.5s |
| `UserPromptSubmit` | BM25 + embedding 检索 → 注入 HOT/WARM 规则 → 写 gate marker | ≤ 500ms |
| `PreToolUse` | 读 marker → block 一次 → 删除 marker | ≤ 50ms |
| `SessionEnd` | 写 extract job 文件（异步提取排队） | ≤ 200ms |

两个核心机制：

1. **规则注入** — 每次 prompt 时检索匹配的规则，按 HOT/WARM 分层注入为 `additionalContext`
2. **Gate 阻断** — 高置信度 + active 规则命中时，阻断 Edit/Write/Bash 等工具调用，强制 Claude 先看到规则再重试

---

## 安装

```bash
# 从源码安装（开发模式）
git clone https://github.com/KorenKrita/nokori.git
cd nokori
pip install -e .

# 注册 hooks 到 Claude Code
nokori install

# 验证
nokori health
nokori status
```

`nokori install` 会合并 hooks 到 `~/.claude/settings.json`，不覆盖你已有的其他 hooks。

```bash
# 预览将要写入的变更
nokori install --dry-run

# 卸载（只移除 nokori 的 hooks，保留其他）
nokori install --uninstall

# 临时禁用（hooks 保留但不执行）
nokori install --disable
nokori install --enable
```

---

## 快速开始

### 1. 手动添加一条规则

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

### 2. 模拟检索

```bash
nokori test "I'll just git push --force this branch"
```

输出：

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

### 3. 在真实 session 中体验

正常使用 Claude Code。当你的 prompt 匹配到规则时：
- Claude 会在回复前看到注入的规则上下文
- 如果规则是 HOT + high confidence + active，第一次工具调用会被 block
- Claude 看到 block reason 后会调整行为，重试时自动放行

### 4. 规则过时了？

```bash
# CLI 方式（无时间限制）
nokori dismiss abc123

# 或在对话中说
# "dismiss abc123"
# （限最近 24h 内注入过的规则）
```

---

## 自动提取

配置 LLM 后，Nokori 可以从 Claude Code 的 session transcript 中自动提取规则：

```bash
# 配置 LLM（任何 OpenAI-compatible 端点）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手动提取（指定 transcript）
nokori extract --session ~/.claude/projects/.../session.jsonl

# 或 dry-run 预览
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 消费所有待处理的 extract jobs
nokori extract
```

提取流程：读 transcript → 压缩（保留用户消息，截断 AI 响应）→ LLM 提取候选规则 → 与已有规则合并（SAME/BROADER/CONTRADICTS/UNRELATED）。

没有配置 LLM 时，Nokori 会尝试 `claude -p --model haiku` 作为 fallback。

---

## 规则生命周期

```
candidate → active → dormant → (reactivated or archived)
                  ↘ merged
```

| 状态 | 检索 | Gate | 触发条件 |
|------|------|------|----------|
| `candidate` | 不参与 | 否 | LLM 提取的 medium confidence 规则 |
| `active` | 正式池 | HOT 时是 | 用户纠正 / evidence_score ≥ 2 跨 ≥ 2 天 |
| `dormant` | 正式池（最高 WARM） | 否 | 30 天未 HOT 命中 |
| `merged` | 不参与 | 否 | 被更新的规则取代 |
| `archived` | 不参与 | 否 | 用户 dismiss / candidate 超时 |

### 激活条件

- 用户明确纠正（high confidence correction）→ 立即 active
- 纯 AI evidence：`evidence_score >= 2` 且跨 `>= 2` 个活跃天

### Global Promotion

当一条 project 规则在 3 个不同项目中被 HOT 命中（`unique(project_id, date) >= 3`），自动升级为 global 规则，所有项目受益。`preference` 类型不参与升级。

### 维护

维护任务在 `SessionStart` 时自动触发（按间隔检查）：

- **Dormant 扫描**（每 7 天）：30 天未命中的 active → dormant
- **Candidate 清理**（每 30 天）：超时未确认的 candidate 删除
- **解合并检查**（每 90 天）：merged 指向的目标已 dormant/archived → 恢复为 dormant

也可手动触发：

```bash
nokori maintain
```

---

## 检索引擎

### BM25（默认，零依赖）

- Latin text: lowercase word tokens（≥ 2 chars）
- CJK text: char bigrams
- 混合文本自动切换

### Embedding（可选，rules ≥ 20 时自动启用）

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
export NOKORI_EMBED_DIMENSIONS=384
```

启用后使用 RRF（Reciprocal Rank Fusion）融合 BM25 和 embedding 结果。Embedding API 失败时自动 fallback 到纯 BM25。

### 注入分层

| 层级 | 条件 | 注入内容 |
|------|------|----------|
| HOT | top-1 且显著高于 top-2 + 最低证据通过 | trigger + action + rationale |
| WARM | top-5 内其余 | trigger + action 一行 |
| COLD | top-5 外 | 不注入 |

注入预算：1500 chars（规则）+ 500 chars（热缓存，独立）。

---

## CLI 完整参考

```bash
# 规则管理
nokori add [--trigger "..." --action "..." --source-type ... --confidence ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--action ...] [--confidence ...] [--status ...]

# 提取
nokori extract [--session <path>] [--dry-run]

# 调试
nokori test "<prompt>" [--project <id>]
nokori status
nokori logs
nokori health

# 维护
nokori maintain
nokori reset

# 导入导出
nokori export <path.json>
nokori import <path.json>

# 安装
nokori install [--dry-run | --uninstall | --disable | --enable]
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NOKORI_DATA_DIR` | `~/.nokori` | 数据根目录 |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | 注入字符上限 |
| `NOKORI_GATE_ENABLED` | `1` | 启用 gate |
| `NOKORI_GATE_TTL_SECONDS` | `600` | Marker 过期时间 |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | Gate 拦截的工具 |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions 端点 |
| `NOKORI_LLM_MODEL` | — | LLM 模型名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_EMBED_ENABLED` | `0`（rules≥20 自动） | 强制启用 embedding |
| `NOKORI_EMBED_BASE_URL` | — | OpenAI-compatible embeddings 端点 |
| `NOKORI_EMBED_MODEL` | — | Embedding 模型名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `384` | 向量维度 |
| `NOKORI_EMBED_CHUNK_SIZE` | `512` | 文本分块字符数 |
| `NOKORI_EMBED_CHUNK_COUNT` | `3` | 每规则最多分块数 |
| `NOKORI_DISABLED` | `0` | 完全禁用 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | Dismiss 触发词 |
| `NOKORI_LOG_LEVEL` | `warn` | 日志级别 |

所有 LLM/Embedding 端点兼容：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任何 `/v1/chat/completions` + `/v1/embeddings` 端点。

---

## 配置文件

除环境变量外，Nokori 支持 TOML 配置文件 `~/.nokori/config.toml`（路径随 `NOKORI_DATA_DIR`）。

**优先级**：环境变量 > config.toml > 内置默认值。

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
dimensions = 384
chunk_size = 512
chunk_count = 3
enabled = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
```

所有字段与环境变量一一对应。文件不存在时静默忽略，纯环境变量模式照常工作。

---

## 数据存储

所有数据存储在本地 `~/.nokori/`：

```
~/.nokori/
├── config.toml           # 配置文件（可选，env vars 优先）
├── rules.db              # SQLite (WAL mode): 规则 + 索引 + 元数据
├── jobs/                 # Extract job 队列
├── active_sessions/      # Session registry
├── pending-ack-*.marker  # Gate markers (短生命周期)
├── logs/
│   ├── hook.log          # Hook 进程日志
│   └── pipeline.log      # 提取/合并日志
└── cache/
```

- 零网络同步，纯本地
- 规则不包含源代码，只含行为描述
- LLM 调用发送压缩后的 transcript 片段（非源代码）
- 可指向本地 Ollama 实现完全离线

---

## 与现有系统的关系

| 系统 | 关系 |
|------|------|
| CLAUDE.md | 互补。Nokori 不修改 CLAUDE.md，规则是动态的行为约束 |
| Claude Code auto-memory | 不冲突。memory 存事实，Nokori 存行为规则 |
| 其他 memory 插件 | hooks 不覆盖，但建议不同时运行多个 memory 类插件 |

---

## 开发

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3.10+ -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

项目约束：
- 零运行时依赖（`dependencies = []`）
- 纯 Python stdlib + urllib 调用 API
- 交互热路径（UserPromptSubmit / PreToolUse）禁止 LLM 调用
- 所有 hooks 顶层 try/except，失败返回 pass-through

---

## License

MIT
