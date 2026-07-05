# 自动提取

[← 返回主文档](../../README.zh-CN.md)

---

关会话后运行，不在交互热路径上。配置 LLM 后，Nokori 读取该场对话的 transcript，提取可能的规则，再让每条候选走完冷路径飞轮。Claude Code、Cursor 与 OMP 走同一条提取管线；在 OMP 中，已安装的 TypeScript bridge 会在 `session_shutdown` 时通过 session manager 取到当前 session 文件，并把本地 `~/.omp/agent/sessions/**/*.jsonl` 路径交给现有 Python dispatcher。

```bash
# 配置 LLM（任何 OpenAI-compatible 端点）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手动提取
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session ~/.omp/agent/sessions/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# dry-run 预览
nokori extract --session ~/.omp/agent/sessions/.../session.jsonl --dry-run

# 消费所有待处理 job
nokori extract
```

---

## 一条 transcript 怎么变成规则

冷路径故意比热路径啰嗦。它宁愿多判几轮，也不愿把一条含糊规则直接塞进正式池：

1. **读** transcript，单文件上限 50MB

   OMP session 日志位于 `~/.omp/agent/sessions/**/*.jsonl`；由 `session_shutdown` 触发后，bridge 会通过 session manager 把当前文件交给同一套压缩 / 提取流程。
2. **压缩**：用户消息原样保留，AI 回复砍成头 200 字 + 尾 100 字；整体再压到约 30k token
3. **提取**：extractor 角色输出结构化候选
4. **判定 / 重写 / 再判定**：admission judge 与 final judge 拒绝弱证据/过宽规则
5. **合并规划**：merge planner 与邻近规则比较关系
6. **验证入库**：归档指纹、matcher 编译、cold-fast-lane 阈值决定存为 candidate 还是 active
**LLM 调用格式**：每个角色拆成 system + user 两条消息。transcript 片段包在 `--- BEGIN UNTRUSTED DATA ---` / `--- END UNTRUSTED DATA ---` 分隔块中。

---

## Merge 策略

LLM 给每条候选回一个关系字母 `A`–`E`：

| 判定 | 行为 |
|------|------|
| **SAME (A)** | merge_into_existing / replace / reject |
| **BROADER (B)** | 安全/质量判断后决定 |
| **NARROWER (C)** | 插入新规则，与已有共存 |
| **CONTRADICTS (D)** | 保守 keep_both 或 reject_new |
| **UNRELATED (E)** | 插一条新 candidate |

失败处理：

- **提取 LLM 失败**：job 保持 pending
- **Merge LLM 失败**：当前候选跳过，job 保持 pending

**邻居回填**：BM25 预筛不足 5 条时，按 `updated_at` 补上最近更新的规则。

---

## Async Extract Mode

```bash
export NOKORI_EXTRACT_MODE=async
```

| 模式 | 行为 |
|------|------|
| `manual`（默认） | 关会话只落待办文件，需手动 `nokori extract` |
| `async` | 关会话时后台直接跑 extract |

日志：`~/.nokori/logs/async-extract.log`。未配置 LLM（`NOKORI_LLM_BASE_URL` 未设置）时，async 模式会尝试调用本机 `$PATH` 中的 `claude -p` CLI 作为兜底。

边缘情况：

- `extract.lock` 被占：不自动启动，pending job 保留
- Transcript mtime 变了：刷新 job mtime，继续保留 pending
- 损坏的 job 文件：挪到 `jobs/bad/`
- `NOKORI_EXTRACT_DEFER_ACTIVE=1`：有其它 open session 时只写 job 不 fork

---

## Fork 缓存提取（仅 Claude Code）

```bash
export NOKORI_EXTRACT_FORK_CACHE=1
```

在 `async` 模式下启用后，Claude Code session 结束时会 fork 原 session（`claude -r <session-id> --fork-session`）复用 prompt cache 做提取，长对话 input token 成本降低约 90%。

**工作流程：**

1. Session 结束 → `session_end` hook 检测到 `Host.CLAUDE`
2. 后台启动 `fork_runner`
3. 检查 byte offset：如果之前已部分提取，读取 offset 前第 3 条 user message 作为锚点，告诉模型只提取锚点之后的新内容
4. 压缩检测：如果 offset 之后存在 `compact_boundary`（上下文已被压缩），跳过 fork 回退到正常读 transcript 原文的路径
5. Fork session，prompt 带角色覆盖指令强制执行提取行为
6. 解析 JSON 输出 → 冷管道（admission → rewrite → merge → insert）

**前置条件：**

- `claude` CLI 在 `$PATH` 中
- `extract.mode = "async"`
- `extract.fork_cache = true`
- 仅 Claude Code session 生效（Cursor session 始终走正常路径）

**回退：** CLI 不可用、session ID 无效、fork 超时（300s）、输出非法 JSON 时，自动回退到正常的 `nokori extract` async 路径。

日志：`~/.nokori/logs/fork-extract.log`
