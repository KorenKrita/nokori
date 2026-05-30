# Nokori (残り)

> 经验留下的痕迹，比记忆更深的东西。

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
| `SessionStart` | 热缓存（上一场未 extract 的尾部 user 消息）+ 轻维护 | ≤ 1.5s |
| `UserPromptSubmit` | 正式池注入 + gate marker；与影子池合并检索（仅影子 HOT 计 promotion） | ≤ 500ms |
| `PreToolUse` | 读 marker → block 一次 → 删除 marker | ≤ 50ms |
| `SessionEnd` | 写 extract job 文件（异步提取排队） | ≤ 200ms |

两个核心机制：

1. **规则注入** — 每次 prompt 时检索匹配的规则，按 HOT/WARM 分层注入为 `additionalContext`
2. **Gate 阻断** — 仅 **correction / anti_pattern** 且 HOT + high + active 时阻断工具；**solution 等可注入提示但不拦截**（见 [注入 vs 阻断](#注入-vs-阻断)）

---

## 安装

```bash
# 从源码安装（开发模式）
git clone https://github.com/KorenKrita/nokori.git
cd nokori
pip install -e .

# 可选：安装本地 embedding 支持
pip install -e ".[local-embed]"

# 注册 hooks 到 Claude Code
nokori install

# 验证
nokori health
nokori status
nokori logs          # hook / pipeline / async-extract 日志
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

不传 `--project-id` 时写入 `project_scope=global`（所有项目正式池可见）。传了则 `project_scope=project` 并绑定该 `project_id`。

### 2. 模拟检索

```bash
nokori test "I'll just git push --force this branch"
# 默认 project_id = 当前目录 git 根（与 hook 一致）；可用 --project 覆盖
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
- Claude 会在回复前看到注入的规则上下文（HOT/WARM，含 solution 等类型）
- 仅 **correction / anti_pattern** 且 HOT + high + active 时，第一次匹配工具调用会被 block；**solution 等只提示、不拦截**
- 被 block 后 Claude 看到原因，重试同工具时自动放行

### 4. 规则过时了？（Dismiss）

每条规则有一个 **short_id**（如 `a3f2b1`），在注入文案和 Gate 阻断理由里都会出现。规则若已不适用，应**退役**（状态变为 `archived`，不再检索、不再 Gate）。

**方式一：终端（随时可用）**

```bash
nokori dismiss a3f2b1
```

**方式二：在对话里说一句话（配合 Gate / 注入提示）**

当某条规则刚被注入，或 Claude 被 Gate 拦住时，提示里会写：可以说 `dismiss <short_id>` 来退役。你在**下一条用户消息**里写：

```text
dismiss a3f2b1
```

`UserPromptSubmit` hook 会识别并归档该规则。

| 对比 | CLI `nokori dismiss` | 对话里 `dismiss <short_id>` |
|------|----------------------|-----------------------------|
| 时间限制 | 无 | 仅 **当前 session** 且 **过去 24 小时内** 注入过的规则 |
| 动词 | 固定子命令 | 可配置，见 `dismiss_phrase`（默认 `dismiss`） |

若把 `dismiss_phrase` 改成 `forget`，对话里应写 `forget a3f2b1`（`nokori dismiss` 子命令名不变）。格式固定为：**一个单词 + 空格 + short_id**，不是整段自然语言。

配置：`dismiss_phrase` / `NOKORI_DISMISS_PHRASE`，见 [配置文件](#配置文件) 与 [config.toml.example](config.toml.example)。

---

## Gate 与 PreToolUse：两层「工具匹配」

很多人以为只有一个「Gate 拦截工具」开关，其实是**两层**，配置位置和内容都不同：

```
Claude 准备调用工具
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第一层：Claude Code settings.json 的 PreToolUse.matcher │
│ 「要不要执行 nokori hook pre-tool-use」                    │
│ 默认：Edit|Write|MultiEdit|Bash|NotebookEdit            │
│ Read / Grep 等默认不会进 hook                            │
└─────────────────────────────────────────────────────────┘
    │ hook 已执行
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第二层：Nokori [gate].matcher（NOKORI_GATE_MATCHER）       │
│ 「hook 里要不要对这次 tool_name 做 block」               │
│ 默认：同上；须为 Python 正则，对 payload.tool_name fullmatch│
└─────────────────────────────────────────────────────────┘
    │ 有 marker 且匹配
    ▼
  deny 一次 → 删 marker → 重试同工具则放行
```

Gate 阻断时 hook 返回 Claude Code 官方格式（[Hooks reference — PreToolUse](https://code.claude.com/docs/en/hooks)）：`hookSpecificOutput.permissionDecision: "deny"` 与 `permissionDecisionReason`（展示给 Claude）。顶层 `decision`/`reason` 对该事件已弃用，Nokori 不再输出。

### 第一层：让 hook 在哪些工具上运行

- **配置文件**：`~/.claude/settings.json`（`nokori install` 写入，不会读 `config.toml`）
- **字段**：`hooks.PreToolUse` 里 nokori 那条的 `matcher`
- **默认值**（install 时）：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成「任意工具都跑 hook」**：把该条的 `matcher` 改为 `*`（Claude Code 约定，表示所有 PreToolUse 事件）

示例（仅示意 nokori 那条，保留你其它 hooks）：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "nokori hook pre-tool-use",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

已安装过的话需**手动改** settings，或 `nokori install --uninstall` 后再 `install`（会按仓库内默认 matcher 写回，不是 `*`）。改完后无需改 `config.toml`。

### 第二层：hook 内对哪些 tool_name 真正 block

- **配置文件**：`~/.nokori/config.toml` 的 `[gate] matcher`，或环境变量 `NOKORI_GATE_MATCHER`
- **含义**：hook 已被调用时，用 **Python `re.fullmatch`** 匹配 payload 里的 `tool_name`
- **默认值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成「凡进 hook 的工具都参与 block 判断」**：设为 `.*`（**不要**写字面量 `*`，在正则里非法）

```toml
[gate]
matcher = ".*"
```

仅改这一层、不改 settings 时：Read 等仍**不会**进 hook，自然也不会被 block。两层要一起改才能达到「任意工具都可能被 Gate」。

### 注入 vs 阻断

| | 注入（`additionalContext`） | Gate（PreToolUse deny） |
|--|------------------------------|-------------------------|
| 规则范围 | 正式池 HOT + WARM | 正式池 HOT 的子集 |
| `source_type` | 全部（含 solution、preference） | 仅 **correction**、**anti_pattern** |
| 其它条件 | 检索分层达标 | 且 **high** + **active** |

例如 `solution` 规则可以出现在 HOT 提示里，但**不会**因为 Gate 拦住你的第一次 Write/Bash。

### 其它 Gate 相关配置

| 项 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 总开关；关则只注入、不 block |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | marker 有效期（默认 600s），过期不再 block |

**Prompt-hash 不匹配（fail-open）**：`UserPromptSubmit` 写入 marker 时记录当前 prompt 的 hash；`PreToolUse` 用 injections 表或 payload 解析当前 hash。若无法解析或与 marker 不一致（用户已发下一条消息），**删除 marker 并放行工具**，不 block。

---

## 自动提取

配置 LLM 后，Nokori 可以从 Claude Code 的 session transcript 中自动提取规则：

```bash
# 配置 LLM（任何 OpenAI-compatible 端点）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手动提取（指定 transcript；project 优先用 SessionEnd job 里记录的 project_id）
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# 或 dry-run 预览
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 消费所有待处理的 extract jobs
nokori extract
```

提取流程：读 transcript → 压缩（保留用户消息，截断 AI 响应）→ LLM 提取候选规则 → 与已有规则合并（SAME/BROADER/CONTRADICTS/UNRELATED）。

**Merge 判定（实现）** — LLM 关系字母 `A`–`E` 对应 SAME / BROADER / NARROWER / CONTRADICTS / UNRELATED：

| 判定 | 行为 |
|------|------|
| **SAME (A)** + 已有 `candidate` | 加 evidence；high correction 可立即 activate，否则按 evidence 规则激活 |
| **SAME (A)** + 已有 `active` / `dormant` | **不新建规则**；对已有行 `add_evidence(..., "same_extraction", 1)`，保留全部历史 |
| **BROADER / CONTRADICTS (B/D)** | 插入新规则并 `supersede` 旧规则；若同轮已对另一条判 **A**，则 `supersede` 到 A 那条，不另插第二条 active |
| **NARROWER / UNRELATED (C/E)** | 共存，无操作 |
| 无强关系 | 插入新 `candidate` |

**Merge LLM 失败**：若已有邻近规则但关系判断 LLM 调用失败，**不会插入**该候选；`nokori extract` **不**标记 transcript 已提取，extract job **保持 pending** 以便重试。

**邻居回填（v0.1 故意保留）**：BM25 预筛不足 5 条时，会再塞入按 `updated_at` 最近的规则再送 LLM，可能多耗 token、出现大量 UNRELATED——用于减少「零词重叠」漏合并；无开关。取舍见本地 `docs/design-decisions.md`（`docs/` 默认不进 Git）。

没有配置 LLM 时，Nokori 会尝试 `claude -p --model haiku` 作为 fallback（prompt 经 stdin，不进 argv）。

---

## 数据库

- SQLite `rules.db`，首次使用时自动创建
- 若数据库与当前 nokori 版本不兼容，会报错；请先 `nokori export` 备份，或换新 `NOKORI_DATA_DIR` / `nokori reset`

## 规则生命周期

```
candidate → active → dormant → (reactivated or archived)
                  ↘ merged
```

| 状态 | 检索 | Gate | 触发条件 |
|------|------|------|----------|
| `candidate` | 不参与 | 否 | LLM 提取的 medium confidence 规则 |
| `active` | 正式池 | HOT 时是 | 用户纠正 / evidence_score ≥ 2 跨 ≥ 2 天 |
| `dormant` | 正式池（命中时当轮最高 WARM，不 gate） | 否 | 30 天未「强相关命中」（见下 `last_hit`） |
| `merged` | 不参与 | 否 | 被更新的规则取代 |
| `archived` | 不参与 | 否 | 用户 dismiss / candidate 超时（日历天，见维护） |

### 激活条件

- **手动 `nokori add`** 或 **提取合并时**：`high` + `correction` 候选 → 直接 `active`（含初始 `user_correction` 证据）
- 纯 AI evidence（含跨项目 `shadow_hot`）：`evidence_score >= 2` 且跨 `>= 2` 个活跃天

**`last_hit` 语义**：用于 dormant 扫描（`last_hit` 缺失时用 `created_at`）。在以下情况更新：**(1)** 正式池 HOT 注入；**(2)** dormant 规则检索达标、当轮再激活。普通 WARM 注入**不**更新 `last_hit`。`hit_count` 仍仅 HOT 注入 +1。

**Dormant 再激活**：检索分达 HOT 档时，**当轮**仍按 WARM 注入（无 gate）；DB **当轮**即 `status=active` 并更新 `last_hit`，**下一轮**可 HOT + gate（若类型为 correction/anti_pattern）。与 `UserPromptSubmit` hook 行为一致。

### Project ID

Nokori 通过 `git rev-parse --show-toplevel` 解析项目根目录，生成 `<目录名>-<路径hash前8位>` 作为 project_id。不同路径的同名仓库不会冲突。非 git 目录 fallback 为 cwd 路径 hash。

### Global Promotion

每次 `UserPromptSubmit` 对**正式池 ∪ 影子池**做一次检索（BM25 + 可选 embedding RRF），再按池拆分：仅正式池 HOT/WARM 注入；影子池 **仅 HOT** 计 `record_shadow_hit`。**≥3 个不同 project_id** 命中后升为 `global`（**无二次确认**，v0.1 产品选择）。`preference` 不参与。

### Shadow Pool（影子池）

其他项目的 high-confidence correction/anti_pattern/solution **active** 规则与正式池同一次检索（`fetch_shadow_rules`），不单独阻塞响应。

**本项目正式池为空时仍会跑影子池**（场景 C：新项目可从他项目 shadow HOT 累积 promotion），只要 `project_id` 可解析且 promotion 开启（`NOKORI_PROMOTION_ENABLED=1` 或 `[promotion] enabled = true`）。设为 `0` / `false` 时**完全不跑**影子池，场景 C 不可用——这是显式关 cross-project 学习的开关，不是 bug。

**影子命中**：与正式池相同检索管线（含 embedding RRF，当可检索规则数 ≥20 且已启用时）→ `tier_results` → **仅 HOT** 才计 hit；不注入当前 session。

命中时（**每个「其它项目 × 当天」至多计 1 次**；同日同项目重复 HOT 不叠加 `shadow_hit_count`）：
- 记录 shadow hit（`shadow_hit_count` = 去重后的跨项目 HOT 次数；**升 global 看 `promotion_evidence` 里不同 `project_id` 数**，不是 hit 总数）
- 给规则 evidence_score +1（`shadow_hot` 证据；用于 candidate 激活）
- 不注入到当前 session（不影响用户体验）

这使得跨项目的规则升级完全基于检索证据驱动。

查看进度：`nokori status` 会列出已有 shadow 命中的 project 规则（`short_id  N/3  projects=[...]`），以及当前 `global` 规则总数。

### Async Extract Mode

```bash
export NOKORI_EXTRACT_MODE=async
```

设为 `async` 后，SessionEnd 时若 **没有** 其它进程正持有 `extract.lock`，会 fork 后台 `nokori extract`；若已在跑则**只排队 job、不重复 spawn**（避免 `(extract already running)` 噪音）。子进程 stderr 追加到 `~/.nokori/logs/async-extract.log`。`NOKORI_EXTRACTING` 仅在 `claude -p` fallback 子进程内设置，用于防止 hook 递归；async 提取子进程本身**不**设置该变量，以便正常调用已配置的 LLM API。spawn 失败不影响 session 关闭。

默认 `manual` 模式下只写 job 文件，需手动 `nokori extract` 消费。

若 SessionEnd 之后 transcript 仍被追加（文件 `mtime` 变化），`nokori extract` 会**刷新 job 的 mtime 并保留 pending**，不会静默丢弃 job。

可选：`NOKORI_EXTRACT_DEFER_ACTIVE=1` 时，async 模式下若仍有**其他未 SessionEnd 的 session**（`active_sessions/` 里 `ended_at` 为空，`count_open_sessions`），当前 SessionEnd **只写 job、不 fork** `nokori extract`；待其它 session 结束后再手动或下次 SessionEnd 触发提取。

`NOKORI_SESSION_IDLE_SECONDS`（`[session] idle_seconds`）**不参与** defer，仅用于 `nokori status` 的「active」展示（open + 近期有 `touch` 心跳）。

Extract jobs 仅由 `nokori extract`（手动或 async 子进程）消费，SessionStart 不处理 jobs。`nokori extract` 使用 `{data_dir}/extract.lock`（Unix / Windows 均支持）防止并发重复处理。

### 热缓存

SessionStart 在**当前** `transcript_path` **同目录**下，取 mtime 严格早于当前文件的最新 `*.jsonl` 作为上一场（**不**查 `active_sessions/`）；若该文件在 `extract_state` 中尚未以当前 mtime extract 过，则注入最后 3 条 user 消息（500 chars，独立预算）。并发多 session 同目录时可能不是语义上的「上一场」——v0.1 接受该启发式；详见本地 `docs/product-spec.md` / `docs/design-decisions.md`。

**Shadow 与 candidate 激活**：跨项目 shadow HOT 会 `add_evidence(..., shadow_hot, 1)`。若其它项目的规则仍是 `candidate`，多次（不同天）shadow 命中可能凑够纯 AI 激活条件（score≥2 且 2 个活跃日）——**与「只服务 promotion」的直觉不同，v0.1 有意允许**跨项目检索证据参与激活。

### 维护

维护任务在 `SessionStart` 时自动触发（按间隔检查）：

- **Dormant 扫描**（每 7 天）：30 天未命中的 active → dormant
- **Candidate 清理**（扫描间隔最多每 30 天跑一次）：删除 **created_at ≥20 日历天** 的普通 candidate、**≥40 天** 的 `anti_pattern` candidate（非「活 30 天」）
- **Unmerge 检查**（最多每 90 天）：`status=merged` 的规则若 `superseded_by` 指向的规则已删除或 dormant/archived，则恢复为 `dormant`（避免赢家消失后永远卡在 merged）
- **Injection 清理**（扫描间隔最多每 7 天）：删除 **30 天前** 的 `injections` 行（dismiss 仅查 24h，留缓冲）

也可手动触发：

```bash
nokori maintain
```

---

## 检索引擎

### BM25（默认，零依赖）

- Latin text: lowercase word tokens（≥ 2 chars）
- CJK text: 以 bigram 为主；单字 CJK 保留 unigram（提高 recall）
- 混合文本自动切换

### Embedding（可选，可检索规则 ≥ 20 时自动启用）

自动启用条件：**当前检索池**（本条 prompt 的 formal∪shadow 规则数）≥ 20，且已配置远程 embed **或** 已安装 `pip install nokori[local-embed]`。全库 `nokori health` / 索引仍用全局 active+dormant 计数。显式 `NOKORI_EMBED_ENABLED=1` 时在条数不足时也会尝试启用。

**两套阈值（易混淆）**：

| 场景 | 计数范围 | 作用 |
|------|----------|------|
| **SessionStart** `embed` kickstart | 全库 `active+dormant` 条数 | 是否后台拉起 embed server（≥20 即可能 spawn，与你当前项目只有几条规则无关） |
| **UserPromptSubmit** 检索 | 当次 formal∪shadow 池大小 | 本条 prompt 是否走 embedding RRF |

**半索引**：启用 embed 后，**没有** `rule_embeddings` 行的规则在 RRF 里只靠 BM25（刚 activate、import 后未索引、索引失败时会出现）。`nokori health` 的 `embed.index` 会 warn 缺失条数。

远程 API 模式：

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
# NOKORI_EMBED_DIMENSIONS 默认不传（用模型自身维度），仅 OpenAI text-embedding-3 等支持该参数时设置
```

本地模型模式（无需配置 URL）：

```bash
pip install nokori[local-embed]
```

安装 `sentence-transformers` 后，当可检索规则 ≥ 20 且未配置远程 embed endpoint 时，使用本地 **`paraphrase-multilingual-MiniLM-L12-v2`**（118MB，384 维）。模型由 **embed 共享进程**加载到 `~/.nokori/models/`。

Hook 行为（`NOKORI_EMBED_SERVER_AUTO_START=1`，默认开）：

- **SessionStart**：非阻塞 `spawn` embed server（若尚未运行）
- **UserPromptSubmit**：若 server 尚未 `ping` 通 → 后台 spawn、**当轮纯 BM25**；下一轮起通常有 RRF
- 不在 hook 内等待最多 45s 模型加载（避免超过 Claude 10s hook 超时）

`nokori embed start` 可提前拉起；`NOKORI_EMBED_ENABLED=1` 会强制尝试 embed（即使规则 <20），小库首条仍可能 BM25-only。

优先级：远程 API（配了 base_url）> 本地 embed server（装了 `[local-embed]`）> 纯 BM25。server 未就绪时回退 BM25，不在每个 hook 子进程里再加载一遍模型。

启用后使用 RRF（Reciprocal Rank Fusion）融合 BM25 和 embedding 结果。

**平台说明**：本地 embed 仅 **macOS / Linux**（`embed.sock`）。Windows 上为纯 BM25 或远程 `NOKORI_EMBED_BASE_URL`。

本地 embed 管理（Unix）：

```bash
nokori embed start    # 后台拉起共享 server（hook 也会按需自动 start）
nokori embed status   # 进程 / socket / idle 配置
nokori embed stop     # 优雅关闭（SIGTERM + IPC shutdown）
# nokori embed serve  # 前台调试；空闲超过 NOKORI_EMBED_SERVER_IDLE 秒自动退出
```

本地 embed server 的 Unix socket 在 `NOKORI_DATA_DIR` 下，**无 IPC 鉴权**（本机单用户场景可接受；勿把数据目录放在多用户共享路径）。

### 注入分层

| 层级 | 条件 | 注入内容 |
|------|------|----------|
| HOT | top-1 且显著高于 top-2 + 最低证据通过 | trigger + action + rationale |
| WARM | top-5 内其余（含最低证据） | trigger + action 一行 |
| COLD | top-5 外 | 不注入 |

**最低证据**：≥2 个 query token 重叠；或 1 token + trigger variant 命中；或 embedding cosine ≥ 0.55。纯 embedding 命中时 `matched_tokens` 可能为空（仍可通过 cosine 门槛进入 HOT/WARM）。

注入预算：1500 chars（规则）+ 500 chars（热缓存，独立）。

---

## CLI 完整参考

```bash
# 规则管理
nokori add [--trigger "..." --action "..." --source-type ... --confidence ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]

# 提取
nokori extract [--session <path>] [--dry-run]

# 调试
nokori test "<prompt>" [--project <id>]
nokori status          # 含 promotion 进度：每条 project 规则 N/3 个不同 project 已 shadow HOT
nokori logs
nokori health

# 维护
nokori maintain
nokori reset

# 本地 embed 共享进程（Unix；可选）
nokori embed start | stop | status

# 导入导出（JSON 的 version 字段 = rules.db schema，当前为 2）
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
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | **第二层**：hook 内 block 的 `tool_name` 正则（任意工具用 `.*`）；见 [Gate 两层匹配](#gate-与-pretooluse两层工具匹配) |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` 时 async 模式有活跃 session 则推迟 fork extract |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | `active_sessions` 无心跳超过此秒数视为非活跃 |
| `NOKORI_HOT_CACHE` | `1` | SessionStart 热缓存 |
| `NOKORI_PROMOTION_ENABLED` | `1` | 影子池与 cross-project promotion；`0` 关闭场景 C |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | hook 远程 embed 超时（秒） |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | 本地 embed 进程空闲退出（秒） |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | hook 按需自动拉起 embed server |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions 端点 |
| `NOKORI_LLM_MODEL` | — | LLM 模型名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_EMBED_ENABLED` | `0`（active+dormant≥20 自动） | 强制启用 embedding |
| `NOKORI_EMBED_BASE_URL` | — | OpenAI-compatible embeddings 端点 |
| `NOKORI_EMBED_MODEL` | — | Embedding 模型名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0`（不传，用模型默认） | 向量维度（仅支持该参数的模型需要设） |
| `NOKORI_EMBED_CHUNK_SIZE` | `512` | 文本分块字符数 |
| `NOKORI_EMBED_CHUNK_COUNT` | `3` | 每规则最多分块数 |
| `NOKORI_STRICT` | `0` | `1` 时 hook 异常向上抛出（调试；默认 fail-open） |
| `NOKORI_DISABLED` | `0` | 完全禁用 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | 对话里退役规则的动词（`动词 + short_id`）；见 [Dismiss](#4-规则过时了dismiss) |
| `NOKORI_LOG_LEVEL` | `warn` | 日志级别 |

所有 LLM/Embedding 端点兼容：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任何 `/v1/chat/completions` + `/v1/embeddings` 端点。

---

## 配置文件

除环境变量外，Nokori 支持 TOML 配置文件 `~/.nokori/config.toml`（路径随 `NOKORI_DATA_DIR`）。

仓库根目录提供完整模板 **[config.toml.example](config.toml.example)**（全部可配置项、默认值、可选值与说明）。

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
# 远程 OpenAI-compatible API（与下方 server 参数同属一张 [embed] 表，勿重复写两个 [embed] 表头）
base_url = "https://api.example.com/v1"
model = "text-embedding-v4"
api_key = "sk-xxx"
# dimensions = 0  # 不填或 0 = 不传给 API（用模型默认维度）
chunk_size = 512
chunk_count = 3
enabled = true
# 本地 embed 共享进程（未配 base_url 且 pip install nokori[local-embed] 时）
# hook_timeout_seconds = 2
# server_idle_seconds = 3600
# server_auto_start = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
# defer_when_active = false   # 有其他 open session 时推迟 async extract

[hot_cache]
enabled = true

[promotion]
enabled = true

[session]
# idle_seconds = 1800
```

所有字段与环境变量一一对应（见 [config.toml.example](config.toml.example) 速查表）。文件不存在时静默忽略，纯环境变量模式照常工作。

**注意**：`[gate] matcher` 只影响 Nokori hook **内部**是否 block；PreToolUse **是否调用 hook** 由 `~/.claude/settings.json` 决定，见上文 [Gate 两层匹配](#gate-与-pretooluse两层工具匹配)。`dismiss_phrase` 的完整说明见 [Dismiss](#4-规则过时了dismiss)。

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
│   ├── pipeline.log      # 提取/合并日志
│   ├── async-extract.log # async 模式子进程 stderr
│   └── embed-server.log  # 本地 embed server（若启用）
├── models/               # sentence-transformers 模型缓存（local-embed）
├── embed.sock            # 本地 embed IPC（Unix）
└── extract.lock          # extract 单实例锁
```

- 零网络同步，纯本地
- 规则不包含源代码，只含行为描述
- LLM 调用发送压缩后的 transcript 片段（非源代码）
- 可指向本地 Ollama 实现完全离线
- **数据库**：与当前 nokori 版本绑定；换机或升级后若打不开库，请 `nokori export` 备份，或换新 `NOKORI_DATA_DIR` / `nokori reset`。

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
python3.11+ -m venv .venv
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
