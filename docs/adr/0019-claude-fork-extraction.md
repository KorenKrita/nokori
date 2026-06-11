# Claude Code session fork for cached extraction

Status: accepted (2026-06-11)

## Context

nokori 的 extractor 角色需要完整对话转录作为输入。当前流程：SessionEnd hook → 读取 transcript jsonl → 调 gemini-3.5-flash API 提取。

Claude API 有 prompt caching（prefix 匹配，5 分钟 TTL，命中后 input token 成本 -90%）。Session 结束后，对话的完整 prefix（system prompt + 全部消息）仍在缓存窗口内。如果能复用这个缓存做提取，可以大幅降低长对话的提取成本。

## 发现

### 可行性验证（2026-06-10 实测）

1. `claude -r <session-id> --fork-session -p "..."` 可以恢复任意 session 的完整对话历史并追加新消息。
2. `--bare` 模式跳过所有 hooks/CLAUDE.md/auto-memory，不会触发 SessionEnd hook 死循环。
3. `--no-session-persistence` 使 fork 出的 session 不写入磁盘，不污染 resume 列表。
4. 用完整 `EXTRACT_SYSTEM` 作为 system prompt 时，提取质量完全符合 schema 要求（所有字段齐全）。

### 缓存复用的关键约束

- Prompt cache 是**严格前缀匹配**：`[system prompt] → [msg 0] → [msg 1] → ... → [msg N]`。
- 如果用 `--system-prompt` 替换系统提示词 → 前缀完全变化 → 0% 缓存命中。
- 如果用 `--bare` → 系统提示词被清空 → 同样无法匹配原 session 的 prefix。
- **正确做法**：不改 system prompt，把 extraction prompt 放在 `-p`（最新 user message）位置。prefix 完全一致，仅末尾追加一条消息。

### 实测：不换 system prompt，extraction prompt 放 user message

```bash
claude -r "$SESSION_ID" --fork-session --no-session-persistence \
  -p "$EXTRACT_SYSTEM\n\nExtract rules from the above conversation. Output JSON only."
```

结果：提取质量与专用 system prompt 方案一致，所有字段正确填充。模型在原 Claude Code agent system prompt 下仍完美遵从 user message 中的提取指令。

### Hook 循环问题

- 不用 `--bare` 时 SessionEnd hook 会触发 → 可能死循环。
- 解决方案：在 `dispatch()` 或 `session_end.handle()` 入口检查环境变量（如 `NOKORI_EXTRACT_MODE=1`）直接跳过。
- 或者用 `--bare`（牺牲缓存但彻底安全）——两种都验证过可行。

### Host 区分

- `detect_host_from_payload()` 已能区分 Claude Code vs Cursor。
- Claude Code session：可以 fork 复用缓存。
- Cursor session：无法 `claude -r`，只能走现有路径（读 transcript → 调 API）。

### 适用范围

| 角色 | 需要完整对话？ | 适合 fork + cache？ |
|------|:-:|:-:|
| extractor | 是 | 适合 |
| admission_judge | 否（candidate JSON） | 不适合 |
| rule_rewriter | 否 | 不适合 |
| final_judge | 否 | 不适合 |
| merge_planner | 否 | 不适合 |
| posthoc_evaluator | 否（bounded window） | 不适合 |

仅 extractor 一步受益，其余角色输入小，直接调 LLM API 更合适。

## 方案设计

### 调用命令

```bash
NOKORI_EXTRACT_MODE=1 claude -r "$SESSION_ID" \
  --fork-session \
  --no-session-persistence \
  -p "$EXTRACTION_PROMPT"
```

### Python 集成

```python
import subprocess, os

def extract_via_fork(session_id: str, extract_prompt: str) -> str:
    env = {**os.environ, "NOKORI_EXTRACT_MODE": "1"}
    result = subprocess.run(
        ["claude", "-r", session_id,
         "--fork-session", "--no-session-persistence",
         "-p", extract_prompt],
        capture_output=True, text=True, timeout=180, env=env
    )
    return result.stdout  # JSON string
```

### 时序要求

- 必须在 session 结束后 **5 分钟内**调用，否则 prefix cache 过期。
- SessionEnd hook 本身就在 session 结束时立即触发，时序天然满足。

### 成本分析（示例：1.9MB session ≈ ~50K tokens prefix）

| 路径 | input 成本 |
|------|-----------|
| 现有：读 transcript → gemini-3.5-flash | 50K tokens × gemini pricing |
| fork + cache hit：claude sonnet | 50K tokens × 0.1（cached） + output |
| fork + cache miss：claude sonnet | 50K tokens × full price |

长对话收益最大；短对话（<5K tokens）差异可忽略。

## 决定结果

1. **CLI 依赖**：可选依赖，不在 PATH 时静默回退。`extract.fork_cache` 默认 false，opt-in。
2. **环境变量方案**：采用。fork_runner 进程不设 `NOKORI_EXTRACTING`（自己要调 LLM），给 claude 子进程设（防 hook 递归）。不用 `--bare`（会破坏 cache）。
3. **Fallback**：fork 任何失败 → `_try_fork_extract` 返回 False → 正常 `_spawn_async_extract` 运行。extract job 始终写入队列供后续 cron 消费。
4. **模型**：不传 `--model`。cache 是 per-model 的，必须用和原 session 相同的模型才能 hit。用户默认模型大概率就是刚结束的 session 用的模型。

## 实现后补充发现

- **offset 增量**：通过 `last_byte_offset` 读锚点（往前 3 条 user message），prompt 里告知模型只提取锚点之后的新内容。
- **压缩检测**：扫描 transcript 中 offset 之后是否有 `compact_boundary`（`subtype: "compact_boundary"`），有则跳过 fork。
- **环境继承**：fork 的 claude 子进程继承完整 `os.environ`（保留 ANTHROPIC_BASE_URL、proxy、caching flags 等用户配置），仅覆盖 `NOKORI_EXTRACTING=1` 和 `NOKORI_DATA_DIR`。
- **安全**：session_id 正则验证 `[a-zA-Z0-9_-]{1,128}`；anchor_text 用 UNTRUSTED 标记包裹且清洗 CLOSE 标记；输出 json.loads 校验。

## 已验证的事实

- `--bare` 确实跳过所有 hooks（实测 nokori logs 无新增事件）。
- `--no-session-persistence` 确实不产生持久化 session 文件。
- `--fork-session` 不修改原 session 状态。
- extraction prompt 放 user message 时，模型不受原 system prompt 中 agent 指令干扰。
- `detect_host_from_payload` 可靠区分 Claude Code / Cursor / Unknown。
