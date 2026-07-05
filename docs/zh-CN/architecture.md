# 架构详解

[← 返回主文档](../../README.zh-CN.md)

---

## 自治质量飞轮

Nokori 的核心是 autonomous quality flywheel（自治质量飞轮）：每条 rule（规则）都要先证明自己，才能从 memory（记忆）变成 behavior（行为）。

这个循环刻意分成三段：

- **Cold path（冷路径）**：关会话后，多角色 LLM 流水线负责提取、判定、重写、合并与评测候选规则。弱规则挡在门外，太宽的规则收窄，不安全的合并会被拒绝或拆分。
- **Hot path（热路径）**：聊天时，hook 只做确定性的检索、匹配、打分、标记读写与 fail-open（失败放行）。你的 prompt 和 Agent 回复之间没有 LLM 等待。
- **Evidence loop（证据回流）**：HOT/WARM 注入会产生 fire events（触发事件）；candidate/suppressed 的影子命中会产生反事实证据；maintenance（维护任务）根据评估后的 evidence（证据）执行生命周期迁移。

让这个循环真正有用的是：

- **Structured triggers（结构化触发器）**：concepts（概念）、required concept groups（必需概念组）、trigger variants（触发变体）、excluded contexts（排除上下文）、tool tags（工具标签）、severity（严重度）、source origin（来源）、runtime policy version（运行时策略版本）与 lineage metadata（谱系元数据），而不是几段松散文本。
- **Autonomous lifecycle（自治生命周期）**：`candidate → active → trusted`，也支持 `suppressed` 恢复和终态 `archived`。手动命令可以 archive（归档），但不能伪造 trust（信任）。
- **Conservative Gate（保守门闸）**：Gate 是给 `trusted + gate_eligible` 规则的一次性提醒刹车，不是权限系统。
- **Hybrid retrieval（混合检索）**：BM25 永远可用；可选 remote embedding（远程向量）或本地 Granite multilingual model 补语义召回；RRF 与 runtime applicability（适用性判断）决定 HOT/WARM。
- **本地优先**：SQLite、hook 日志、job 队列、Gate marker、embedding 权重、Web UI 状态都在 `~/.nokori/` 下。远程 LLM / embedding 端点按需启用。
- **跨工具可观测**：Claude Code、Cursor 与 OMP 都支持；OMP 通过 `~/.omp/agent/extensions/nokori.ts` 这个小型 TypeScript bridge，把 runtime 事件转给同一套 Python dispatcher。`nokori test`、`status`、`health`、`logs`、`extract`、`maintain` 与 Web UI 仍能解释规则为什么触发、为什么没触发。

Nokori 最重要的承诺是 restraint（克制）：它可以早早 reminder（提醒），但必须攒够 evidence（证据）才有资格变得强势；开始帮忙之后，也要继续接受 evidence review（证据审查）。

---

## Hook 时序

Nokori 只有一条热路径，再把不同 runtime 映射进去。Claude Code 与 Cursor 直接调用 Python hook；OMP 则加载 `~/.omp/agent/extensions/nokori.ts` 这个 TypeScript bridge，经 stdin/stdout 复用同一个 dispatcher。检索、Gate marker、job 队列和规则库仍都在 `~/.nokori/`；需要提取时，OMP 会从本地 `~/.omp/agent/sessions/**/*.jsonl` 读取当前 session JSONL。

| Claude Code / Cursor | OMP | 它做什么 | 延迟预算 |
|----------------------|-----|----------|----------|
| `SessionStart` | — | 会话簿记：可选注入上一场 transcript 热缓存，并触发数据库维护 | ≤ 1.5s |
| `UserPromptSubmit` | `before_agent_start` | Agent 开始本轮前：检索规则、注入上下文，必要时写下 Gate marker | ≤ 500ms |
| `PreToolUse` | `tool_call` | 工具调用前：若有 marker 就**拦一次**，随后清除 | ≤ 50ms |
| `SessionEnd` | `session_shutdown` | 会话结束：根据 OMP session manager 报告的当前 session 文件写入待提取任务；`async` 模式下可直接对这个本地 JSONL 跑 extract | ≤ 200ms |

落到实处就两件事：

1. **提醒（注入）**——命中的规矩会经对应 runtime 的注入通道返回，让 Agent 在回复前先看见
2. **拦一次（Gate）**——只有 `trusted` 且 `severity=gate_eligible`、prompt 证据够强、工具输入证据也过关的规则才会拦工具；普通 active 只提醒
---

## 注入 vs 阻断

| | 注入（`additionalContext` / OMP bridge 消息） | Gate（PreToolUse deny / OMP tool block） |
|--|------------------------------|-------------------------|
| 规则范围 | 正式池 HOT + WARM | 正式池 HOT 的子集 |
| 状态 | `active` 与 `trusted` | 仅 `trusted` |
| 严重度 | `reminder`、`high_risk`、`gate_eligible` | 仅 `gate_eligible` |
| 其它条件 | required concepts、excluded contexts、动态 trigger 证据、选择预算都过关 | 还要强 prompt 证据、当前 runtime policy、prompt hash 对得上；工具输入可检查时还要 tool-input 证据 |

Gate 不是权限系统，而是一脚只踩一次的提醒刹车：展示相关规则、拒绝一次、清除 marker，同一条消息里的后续工具调用继续放行。

---

## Shadow Pool（影子池）

每次 `UserPromptSubmit`，Nokori 都分开检索**正式池**和**影子池**，防止影子证据抢走真实提醒的 HOT/WARM 预算。

- **正式池**：`active` + `trusted`；只有这个池能注入
- **影子池**：`candidate` + `suppressed`；永不注入，永不 Gate
- Candidate shadow matches 会变成 candidate → active 的反事实证据
- Suppressed shadow matches 会变成 suppressed → active 的恢复证据

---

## 热缓存

SessionStart 要找「上一场 transcript」，两步走：

1. **优先**读 `{data_dir}/transcript_index/` 里 SessionEnd 写下的 previous/current 指针
2. **回退**：同目录下 mtime 严格早于当前文件的最新那个 `*.jsonl`

若上一场尚未 extract，则从文件**尾部**注入最后 3 条 user 消息（500 字符，预算独立于规则的 1500 字符上限）。

---

## 术语速查

| 词 | 说明 |
|----|------|
| **hook** | Claude Code / Cursor 的 hook，或 OMP bridge 在固定时机触发的一小段命令 |
| **injection**（注入） | 把匹配到的规矩写进 Agent 当轮能看到的上下文里 |
| **Gate**（门闸） | 对 `trusted` + `gate_eligible` 的规矩：第一次匹配的工具调用先 deny 一次 |
| **marker**（标记） | 本轮「请先读 Gate 规则」的临时标记，用一次即清除 |
| **transcript** | 整场对话的 `.jsonl` 日志 |
| **trigger / action** | 规矩的两半：「什么情况下」+「应该怎么做」 |
| **short_id** | 规矩的短编号（如 `a3f2b1`） |
| **dismiss** | 退役一条规矩 |
| **HOT / WARM** | 匹配程度的档位：很相关 / 有点相关 |
| **BM25** | 按关键词重叠打分，零 GPU、默认就有 |
| **embedding** | 按语义相似度打分；可选开启 |
| **RRF** | 把 BM25 榜和向量榜合并成一张总榜的算法 |
| **fail-open** | Nokori 自己出错时不阻断 Claude |
| **extract** | 从 transcript 里用 LLM 提取候选规则 |
| **shadow pool** | 后台匹配 candidate/suppressed 规则：只记证据，不注入 |
| **OpenAI-compatible** | API 地址填 `.../v1` 就能接 Ollama、LM Studio、OpenRouter 等 |
