# Nokori 产品规格（v6；v0.1 内容已重写）

> **做什么**：用户可见行为与验收标准。
> **怎么做**：见 [technical-design.md](./technical-design.md)。
> **取舍与修订**：见 [design-decisions.md](./design-decisions.md)。
> **注意**：旧 v0.1 的 `dormant` / `merged` / “high correction 直接 active” 语义已废弃；当前权威生命周期为 `candidate → active → trusted / suppressed → archived`，详见 [autonomous-rule-quality-flywheel-plan.md](./autonomous-rule-quality-flywheel-plan.md)。

---

## 1. 产品目标

- 从 Claude Code 会话中提炼**可检索、可执行**的规则（trigger → action）。
- 在相似用户 prompt 下**注入**规则上下文，并对高置信 active 规则**阻断一次**工具调用（Gate），迫使模型先阅读规则。
- 交互热路径（hooks）**不调用 LLM**；提取与合并走冷路径（`nokori extract`）。

---

## 2. Claude Code Hooks

| 事件 | 行为 | 失败策略 |
|------|------|----------|
| `SessionStart` | 可选 hot_cache（上一场未 extract 的尾部 **user** 消息）；embed server kickstart；轻量 maintenance | fail-open |

**SessionStart / hot_cache 取舍**（v0.1 故意如此，非缺陷）：

- 「上一场」= 与当前 transcript **同目录**、mtime **严格早于** 当前文件的最新 `*.jsonl`，**不**读 `active_sessions/` 注册表。
- **原因**：实现简单、无额外索引；Claude 默认把 transcript 放在同一 projects 目录，单机开发足够。
- **已知限制**：并发写多个 transcript、或目录里混有无关 `.jsonl` 时，可能不是语义上的「上一 session」——接受该误差，换可靠链路留 v0.2（见 [design-decisions.md](./design-decisions.md)「hot_cache：用 mtime 找上一场」）。
- **关闭**：`NOKORI_HOT_CACHE=0` 或 `[hot_cache] enabled = false`。
| `UserPromptSubmit` | 正式池检索 → HOT/WARM 注入；写 gate marker；影子池仅计 hit | fail-open |
| `PreToolUse` | 读 marker → prompt_hash 校验 → 匹配则 deny 一次并删 marker | fail-open |
| `SessionEnd` | 写 extract job（`status=pending`） | fail-open |

`NOKORI_STRICT=1` 时 hook 异常向上抛（调试用）。

---

## 3. 检索与注入

### 3.1 规则池

**正式池**（注入来源）：

- `project_id` 可解析时：`status IN (active, trusted)` 且 `(project_scope=global OR project_id=当前项目)`。
- 不可解析时：仅 `global` 规则。

**影子池**（不注入，仅 promotion 证据）：

- `candidate` / `suppressed` 影子池：仅记录 shadow evidence，不注入；跨项目 promotion 由 v6 shadow/posthoc 生命周期处理。
- `promotion.enabled=false` 或 `project_id` 为空时不加载影子池。

**合并检索**：正式 ∪ 影子（去重：已在正式池出现的 id 不重复进入 BM25 文档集）→ **一次** BM25（+ 可选 embedding RRF）→ 按池拆分 HOT/WARM。

**影子 HOT 划分**：`shadow_hot` 仅包含 **shadow-only** 规则（`shadow_rules` 中 id 不在 `formal_rules` 的条目）。同一 id 不得同时出现在 `formal_hot` 与 `shadow_hot`。

**跨项目 promotion 取舍**（v0.1 默认开启，非缺陷）：

- **默认** `promotion.enabled=true`：其它项目规则在本项目被检索为 shadow HOT 时累积证据；**≥3 个不同 `project_id`**（按 `promotion_evidence` 去重，非按天计数）自动 `project_scope=global`，**无二次确认**。
- **原因**：多仓库/多项目用户希望「在一个 repo 里踩的坑，别的 repo 也能学到」；阈值 3 平衡误升与冷启动。
- **`preference` 不参与** shadow hit / 提升：偏好类规则项目绑定强，跨项目推广易噪音。
- **关闭**：`NOKORI_PROMOTION_ENABLED=0` — 同时不加载影子池（场景 C 不可用），是显式产品开关，不是实现遗漏。
- 详见 [design-decisions.md](./design-decisions.md)「跨项目 promotion（默认开启）」。

### 3.2 HOT / WARM 分层（top-5）

对融合后 top-5 结果，均需满足**最低证据**之一：

- ≥2 个 query token 与文档重叠；
- 1 token 重叠且 trigger variant 命中；
- embedding cosine ≥ 0.55。

**HOT / GATE**（须同时满足 runtime applicability）：

- v6 matcher 编译成功；required concepts 命中、excluded contexts 未命中；
- trigger evidence 满足 strong phrase path 或 dynamic IDF path；
- `active` 只有在已有 observed usefulness 且 strong evidence 时可 HOT，否则最多 WARM；
- `trusted + gate_eligible` 在 prompt-only 阶段可生成 gate marker；PreToolUse 有 inspectable tool input 时还要复核 tool evidence。

**WARM**：

- active/trusted 规则 trigger evidence 通过但不足以 HOT/GATE；
- 新晋 active（无 `first_observed_useful_at`）只能 WARM；
- candidate/suppressed 命中只记录 shadow evidence，不注入。

**不注入**：`candidate` / `suppressed` 仅作为影子池候选；`archived` 不参与热路径检索。

### 3.3 Embedding 自动启用

- 阈值基于**当次检索池大小** `len(formal ∪ shadow_only)`，非全库条数。
- Hook 路径：本地 embed 用 `kickstart_server()`（spawn 不阻塞）；超时则当轮纯 BM25。
- 规则数 &lt; 20 且未强制 `NOKORI_EMBED_ENABLED` 时不启用 embedding。

---

## 4. Gate

### 4.1 注入 vs 阻断（重要）

| 阶段 | 范围 |
|------|------|
| **注入**（`additionalContext`） | 正式池 **HOT + WARM**；任意 `source_type`（含 `solution`、`preference`）只要检索达标即可展示 |
| **Gate 阻断**（PreToolUse deny） | 仅正式池 **HOT/GATE** 的子集：`trusted` + `severity=gate_eligible` + runtime/tool evidence 通过 |

因此普通 reminder/high_risk 规则可以注入提示，但不会拦截工具；只有 trusted gate_eligible 规则在 runtime evidence 通过时才会写 marker 并可能 block。

### 4.2 Gate 触发条件

- `gate.enabled=true`；
- 至少一条规则满足上表「Gate 阻断」条件（`select_gate_rules`：trusted + gate_eligible）；
- 写入 session marker（含 `prompt_hash`、规则 short_id/action 等）。

### 4.3 PreToolUse 行为

1. `tool_name` 须匹配 `[gate].matcher`（Python `re.fullmatch`，默认 `Edit|Write|MultiEdit|Bash|NotebookEdit`）。
2. 读取 marker；过期或空规则 → 删除 marker，放行。
3. **prompt_hash 校验**（fail-open）：
   - 当前 hash 来源（按序）：payload 的 `prompt` / `user_prompt` → 本 session 最近一条 `injections.prompt_hash` → marker 内 hash。
   - 若**无法解析**当前 hash，或**与 marker 不一致**（用户已发下一条消息）→ **删除 marker，不 block**。
4. 若 tool input 可检查，还要与 marker 中的 trigger/action evidence 匹配；不匹配则保留 marker 并放行本次工具。
5. 校验通过 → `permissionDecision: deny` 一次 → **立即删除 marker**；同一条用户消息内后续工具调用不再 block。

### 4.4 与 Claude Code settings 的两层 matcher

- **settings.json** `PreToolUse.matcher`：决定哪些工具**会执行** nokori hook。
- **config.toml** `[gate].matcher`：hook 内决定对哪些 `tool_name` **真正 deny**。

---

## 5. 提取与合并（冷路径）

### 5.1 流程

读 transcript → 压缩 → LLM 提取候选 → BM25 预筛邻居 → LLM 判关系 → 写库 / activate / supersede。

### 5.2 关系判定（LLM 输出 A–E）

| 字母 | 含义 | 行为 |
|------|------|------|
| **A** | SAME | 见下表 |
| **B** | BROADER | 见下「B/D 与 A 同轮」 |
| **C** | NARROWER | 共存 |
| **D** | CONTRADICTS | 同 BROADER |
| **E** | UNRELATED | 共存 |

**B/D 与 A 同轮**：若同一候选上 LLM 同时对规则 X 判 **A（SAME）**、对规则 Y 判 **B/D**，则 X 走 SAME（叠 evidence / 激活），Y **`supersede` 到 X**（不另插第二条 active），避免两条规则覆盖同一场景。若 **多条**邻居都判 **A**，**第一个** A 为 `anchor_id`，后续 A 仍叠 evidence 但不改锚点。仅 B/D、无 A 时：插入**一条**新规则并 `supersede` 所有 B/D 目标（与 `test_merge_multiple_bd` 一致）。

**SAME (A) 细则**（与实现对齐）：

| 已有规则状态 | 行为 |
|--------------|------|
| `candidate` | 叠 shadow/posthoc evidence；由自动生命周期决定是否 promotion |
| `active` / `trusted` / `suppressed` | 不通过 CLI 手工改状态；merge policy 决定 keep/merge/replace/suppress，必要时 synthetic re-eval |
| 无强关系（本轮无 A/B/D） | 插入新 `candidate` |

**新规则初始状态**（无邻居或 LLM 判无 A/B/D 时插入）：

- `nokori add`：始终插入 v6 `candidate`，写入 `schema_version=6`、当前 `runtime_policy_version`、concepts/groups。
- 冷路径插入：只有通过 matcher 编译、archived fingerprint、merge policy、synthetic eval 与 cold-fast-lane 阈值后才可 `active`；否则为 `candidate` 或 rejected。

~~旧版伪代码：SAME + active/dormant 时新建规则并继承 hit_count~~ — **已废弃**，以实现为准。

**LLM 失败**：关系 LLM 失败、提取 LLM 失败、或提取返回**非 JSON** → **不插入**（或 merge 中止）；transcript 不标 done；job 保持 `pending`。

### 5.3 邻居检索与回填（BM25 + 最近规则）

- 在项目池（`global OR project_id`）内对候选拼 query，**BM25 top-20** 作为 LLM 邻居。
- 若 BM25 命中 **&lt; 5** 条：再按 `updated_at DESC` **回填最近规则**，直至至少 5 条或达 20 条上限（常量 `MERGE_RECENT_FALLBACK=5`、`MERGE_NEIGHBOR_LIMIT=20`）。
- **取舍**：冷路径可接受多调 LLM；回填是为避免「零词重叠」时邻居为空、漏掉应用 SAME/B/D 的机会。副作用是可能把无关规则送进 LLM（大量 **E UNRELATED**、多耗 token）——v0.1 **接受**，不改为「仅 BM25&gt;0 才调 LLM」（见 [design-decisions.md](./design-decisions.md)「Merger：邻居回填」）。
- 若池内**无任何**可合并规则 → 不调 LLM，直接插入新规则。

### 5.4 Extract job

- SessionEnd 写入 `extract-{hash}.json`，`status=pending`。
- `nokori extract` 消费 `list_jobs(status=pending)`；持 `extract.lock` 单实例。
- transcript `mtime` 变化 → 刷新 job，仍 pending。

---

## 6. 规则生命周期（摘要）

| 状态 | 检索（注入） | Gate（阻断） |
|------|----------------|--------------|
| candidate | 否 | 否 |
| active | 正式池 WARM/HOT（无 observed useful 时最多 WARM） | 否 |
| trusted | 正式池 WARM/HOT/GATE | 仅 gate_eligible + runtime/tool evidence |
| suppressed | 影子恢复池，不注入 | 否 |
| archived | 否 | 否 |

- Fire/shadow/posthoc events 是生命周期证据来源；热路径记录完整 rule version、trigger/action snapshot、decision features、runtime policy 与 IDF pool version。
- Global promotion：由 shadow/posthoc evidence 与生命周期控制律处理，不由单次手工 add 触发。
- **Dismiss**（退役规则，不再检索、不再 Gate）：
  - **CLI** `nokori dismiss <short_id>`：规则须在 **过去 24 小时内** 曾被注入过（**任意 session** 的 `injections` 行）。
  - **对话** `dismiss <short_id>`（`dismiss_phrase` 可配置）：须 **当前 session** 且在 **过去 24 小时内** 注入过（`find_rule_id_by_recent_injection`）。
- Archived fingerprints 是负记忆：等价/更宽的新规则默认被阻断；更窄或 scope-changed 的新规则必须有显式 evidence 才能通过。

---

## 7. 配置与数据（用户可见）

- 数据目录默认 `~/.nokori/`（`rules.db`、jobs、markers、logs）。
- `rules.db` 由当前 nokori 在首次使用时创建；库格式与程序版本不匹配时拒绝打开，需 `nokori export` 或新数据目录。
- LLM / Embedding：`base_url` 为 OpenAI-compatible **根 URL，须含 `/v1`**；代码请求 `{base_url}/chat/completions` 与 `{base_url}/embeddings`。
- `extract.mode` 默认 `manual`；`async` 时 SessionEnd 可 fork `nokori extract`。
- `nokori status` 应提示 pending extract jobs 数量（若有）。
- `nokori export` JSON 的 `version` 必须等于当前 `rules.db` schema（`PRAGMA user_version`，当前为 **6**）。
- 维护：candidate TTL **20/40 日历天**（扫描最多每 **30** 天）；`injections` 保留 **30** 天（扫描最多每 **7** 天）；**unmerge** 检查最多每 **90** 天。
- 本地 embed server：Unix socket 在数据目录下，**无 IPC 鉴权**（本机单用户威胁模型，见 design-decisions）。

---

## 8. 非目标（v0.1）

- Hook 路径不等待 embed 模型加载完成（45s）。
- 不做跨仓库 git sandbox。
- Candidate TTL 使用**日历天**，非「活跃天」计数（见 design-decisions）。
- Hot cache 不保证 transcript 目录级「上一 Claude session」语义（见 §2 hot_cache 取舍）。
- Merge 不为省 token 而关闭「BM25&lt;5 时最近规则回填」。

---

## 9. Code review 说明（已知取舍，非待修项）

以下在 2026-05 全维度审查中被标为「可简化 / 可改进」，经产品确认 **v0.1 保持现状**；审查时请对照 [design-decisions.md](./design-decisions.md) 本节锚点，勿重复开 issue：

| 主题 | 结论 |
|------|------|
| 跨项目 shadow 自动提升（默认开、阈值 3、无确认） | **特性**；用 `NOKORI_PROMOTION_ENABLED=0` 关闭 |
| Hot cache 用目录 mtime 找上一场 | **特性**；用 `NOKORI_HOT_CACHE=0` 关闭 |
| Merge BM25&lt;5 时回填最近 5 条再调 LLM | **特性**；无配置开关 |
| TOML 配置层 / Windows extract.lock / LLM 测试注入 | 合理实现，审查已否决「过度设计」 |
| `list_pending` → `list_jobs` 别名 | 公开 API，保留 |
