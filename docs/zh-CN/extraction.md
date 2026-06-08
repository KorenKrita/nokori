# 自动提取

[← 返回主文档](../../README.zh-CN.md)

---

关会话后运行，不在交互热路径上。配置 LLM 后，Nokori 读取该场对话的 transcript，提取可能的规则，再让每条候选走完冷路径飞轮。

```bash
# 配置 LLM（任何 OpenAI-compatible 端点）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手动提取
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# dry-run 预览
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 消费所有待处理 job
nokori extract
```

---

## 一条 transcript 怎么变成规则

冷路径故意比热路径啰嗦。它宁愿多判几轮，也不愿把一条含糊规则直接塞进正式池：

1. **读** transcript，单文件上限 50MB
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

日志：`~/.nokori/logs/async-extract.log`。没配 LLM 会试本机 `claude -p`。

边缘情况：

- `extract.lock` 被占：不自动启动，pending job 保留
- Transcript mtime 变了：刷新 job mtime，继续保留 pending
- 损坏的 job 文件：挪到 `jobs/bad/`
- `NOKORI_EXTRACT_DEFER_ACTIVE=1`：有其它 open session 时只写 job 不 fork
