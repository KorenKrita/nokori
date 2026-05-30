# Nokori (残り)

> 经验留下的痕迹，比记忆更深的东西。

**给 Claude Code 用的「教训笔记本」**——把你纠正过的话、踩过的坑，变成下次能自动翻出来的规矩。

不是记「上次聊了什么」，而是记「下次该怎么做」：相似场景里先提醒 Claude，必要时**拦一次工具调用**，让它先看规矩再改代码。

---

## 它适合谁？

- 总在纠正同一类问题（强推、忘跑迁移、危险命令）的人  
- 希望**跨项目**积累「别这么干」而不是每个 repo 重来一遍的人  
- 接受「规矩存在本地 SQLite、可导出」，不想把整段聊天再喂一遍 LLM 的人  

---

## 一分钟看懂

```
你纠正 Claude
    → Nokori 记下一条规矩（触发场景 + 该怎么做）
    → 下次你的话有点像当时
    → 自动塞进 Claude 的上下文（提醒）
    → 若是高危纠正类且命中很准：第一次改文件/跑命令前先拦一下（Gate）
```

**聊天时** Nokori 尽量快（检索 + 文件，不调 LLM）；**关会话后** 才用 LLM 从 transcript（会话记录）里挖新规矩。

---

## 术语速查

第一次看文档若碰到英文缩写，可先扫这张表；后文仍会重复关键概念。

| 词 | 人话 |
|----|------|
| **hook**（钩子） | Claude Code 在固定时机自动执行的一小段命令（如每次你发送消息前后） |
| **injection**（注入） | 把匹配到的规矩写进 Claude 当轮能看到的上下文里 |
| **Gate**（门闸） | 对少数「高危纠正」类规矩：第一次匹配的工具调用先 **deny**（拒绝）一次，逼 Claude 读规矩 |
| **marker**（标记牌） | 本轮「请先读 Gate 规矩」的临时便签，用一次就撕掉 |
| **transcript** | Claude 整场对话的 `.jsonl` 日志，自动提取规矩时读它 |
| **trigger / action** | 规矩的两半：「什么情况下」+「应该怎么做」 |
| **short_id** | 规矩的短编号（如 `a3f2b1`），用来 dismiss 或对照 |
| **dismiss** | 退役一条规矩（不再检索、不再 Gate） |
| **HOT / WARM** | 匹配程度的档位：很相关 / 有点相关；越热字越多 |
| **BM25** | 按关键词重叠打分，零 GPU、默认就有 |
| **embedding**（嵌入向量） | 按语义相似度打分；规则多了以后可选开启 |
| **RRF** | 把 BM25 榜和向量榜合并成一张总榜的算法 |
| **fail-open** | Nokori 自己出错时**不卡死** Claude，宁可这轮不提醒 |
| **extract** | 从 transcript 里用 LLM **提取**候选规矩（冷路径，不急） |
| **shadow pool**（影子池） | 别的项目里的规矩：只用来统计「是否该升全局」，**不注入到你当前对话** |
| **promotion**（晋升） | 一条项目规矩被多个别的项目认可后，升为 **global**（全局可见） |
| **candidate / active / dormant** | 待确认 → 正在用 → 很久没用先休眠 |
| **merged / archived** | 被新规矩取代 / 你或系统作废 |
| **supersede** | 新规矩顶替旧规矩（旧的状态变 merged） |
| **OpenAI-compatible** | API 地址填 `.../v1` 就能接 Ollama、LM Studio、OpenRouter 等 |

---

## 工作原理

Nokori 在 Claude Code 里挂了 **4 个 hook**；你正常聊天时，它们只在本地查库、算分、读写小文件——**不在 hook 里调 LLM**（否则每次发消息都要等模型，受不了）。

| Hook | 人话 | 延迟预算 |
|------|------|----------|
| `SessionStart` | 开会话：可选带上一场未提取完的 user 尾巴 + 扫一眼库要不要维护 | ≤ 1.5s |
| `UserPromptSubmit` | 你每发一条消息：找规矩 → 注入上下文 → 必要时写 Gate 标记牌 | ≤ 500ms |
| `PreToolUse` | Claude 要用工具前：若有标记牌则 **拦一次**，然后撕掉标记 | ≤ 50ms |
| `SessionEnd` | 关会话：记一个「待提取」任务文件，async 模式可后台跑 extract | ≤ 200ms |

两件核心事：

1. **提醒（注入）** — 命中规矩按 HOT/WARM 写进 `additionalContext`，Claude 回复前就能看到  
2. **拦一次（Gate）** — 只有 **纠正 / 反模式** 且特别准、且高置信、且在用的规矩才会拦工具；**solution（解法类）可以提醒但不拦**（见 [注入 vs 阻断](#注入-vs-阻断)）

---

## 安装

```bash
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

`nokori install` 会把上述 hook 写进 `~/.claude/settings.json`，**合并**进去，不会盖掉你已有的别的插件。

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

下面三步够你感受 Nokori；细节在后面章节。

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

### 2. 模拟检索（不启动 Claude 也能试）

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

### 3. 在真实 session 里试一把

照常开 Claude Code 写代码即可。当你的话和某条规矩比较像时：

- Claude **回复前**会看到注入的规矩（HOT 写得多，WARM 写得短）  
- 若是 **纠正 / 反模式** 且命中特别准：第一次点 Write / Bash 等可能被 **拦一下**，界面里会看到原因和 `short_id`  
- **同一条你的消息里**，拦过一次后，后面再点工具会放行（标记牌已撕掉）  
- **解法类（solution）** 规矩：可以出现在提示里，但**不会拦工具**

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

> **Gate 是什么？** 不是全程禁言，而是「这一轮、第一次动危险工具前，先让 Claude 看见你的规矩」。拦完就撕标记，同一条消息里后面照常干活。

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

关会话后的「慢活」：配置好 LLM 后，Nokori 会读 Claude Code 的 **transcript**（`.jsonl` 会话记录），把里面的纠正总结成候选规矩，再和库里已有规矩合并。

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

> 状态名是英文，含义见 [术语速查](#术语速查)。下面这张表给想细调的人看。

```
candidate（待确认）→ active（在用）→ dormant（休眠）→ 可再激活或 archived（作废）
                              ↘ merged（被新规矩取代）
```

| 状态 | 会参与提醒吗？ | 会 Gate 吗？ | 怎么来的 |
|------|----------------|--------------|----------|
| `candidate` | 否 | 否 | 自动提取、置信度一般，先观察 |
| `active` | 是 | HOT 且类型对时可能 | 你手动 high 纠正，或证据够了自动升 |
| `dormant` | 是，但命中时最多 WARM | 否 | 30 天没被「强相关」用到（见 `last_hit`） |
| `merged` | 否 | 否 | 被更新的规矩取代 |
| `archived` | 否 | 否 | 你 dismiss，或 candidate 放太久被清理 |

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

**人话**：你在项目 A 写代码时，项目 B 里已经验证过的规矩也会参与**打分**，但**不会塞进 A 的对话里**——只用来判断「这条规矩值不值得升成全局」。

- 和当前项目规矩用同一套检索（BM25，规则够多时还有 embedding + RRF）  
- 只有算到 **HOT** 才算一次「影子命中」  
- **每个「别的项目 × 当天」最多记 1 次**（同一天同一项目重复命中不刷分）  
- **≥3 个不同项目**都命中过 → 规矩升为 `global`（全局），不用你点确认  

新项目一个规矩都没有时，只要开了 promotion，影子池仍会跑——方便从零积累跨项目共识。关掉：`NOKORI_PROMOTION_ENABLED=0`。

进度：`nokori status` 里会看到 `shadow_hits` 和 `N/3 projects=...`。

### Async Extract Mode（关会话后自动挖规矩）

```bash
export NOKORI_EXTRACT_MODE=async
```

- **`manual`（默认）**：关会话只写一个待办文件，你自己跑 `nokori extract`  
- **`async`**：关会话时尽量在后台跑 extract（已有进程在跑就只排队，不重复开）  

日志在 `~/.nokori/logs/async-extract.log`。没配 LLM 时会尝试本机 `claude -p` 兜底。

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

> **怎么找到相关规矩？** 先用关键词（BM25），规则多了再加语义向量，最后用 RRF 合并两榜。档位 HOT/WARM 决定写进上下文多少字。

### BM25（默认，零依赖）

- Latin text: lowercase word tokens（≥ 2 chars）
- CJK text: 以 bigram 为主；单字 CJK 保留 unigram（提高 recall）
- 混合文本自动切换

### Embedding（嵌入向量，可选）

规则 **≥ 20 条**（看本条 prompt 要搜的那一批）且配了远程 API 或装了 `pip install nokori[local-embed]` 时，会自动加语义检索。  
`NOKORI_EMBED_ENABLED=1` 可强制尝试（小库也可能首轮仍只用 BM25，见下）。

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

两种分数会经 **RRF**（排名融合）合成一张总榜，再分 HOT/WARM。

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
| CLAUDE.md | 互补。Nokori 不改你的 CLAUDE.md；规矩是动态的「遇到 X 就做 Y」 |
| Claude Code auto-memory | 不冲突。memory 偏事实，Nokori 偏行为规矩 |
| 其他 memory 插件 | hook 可共存，但建议别叠太多「往上下文塞字」的插件 |

---

## 进阶说明

实现取舍、审查共识、与规格书的差异，见仓库内 `docs/design-decisions.md`、`docs/product-spec.md`（`docs/` 默认不进 Git，克隆后本地可读）。

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
