# Nokori 设计决策记录

> v0.1 发布前 code review 共识（2026-05-30）。与 `product-spec.md` 互补：规格写「做什么」，本文写「为什么这样实现 / 已知取舍」。

> **文档对齐**：`product-spec.md` / `technical-design.md` / `README.md` 中过时表述以 ~~删除线~~ + **实现变更** 保留修订过程；`review-history.md` 为历史讨论，未落地项亦同格式标注。

---

## 已修复（实现与规格对齐）

| 项 | 说明 |
|----|------|
| WARM 最低证据 | 规格 §3.2：top-5 均需满足最低证据。`tier_results` 对 HOT/WARM 统一调用 `meets_min_evidence`（lexical ≥2、variant 单 token、或 cosine ≥0.55）。 |

---

## 产品形态（用户可见）

### 手动 `add` 不传 `--project-id` → `project_scope=global`

- **`nokori add` 不传 `--project-id`** → `project_scope=global`，`project_id=NULL`，正式池对所有项目可见。
- **传 `--project-id`** → `project_scope=project`，仅绑定项目 + global 规则参与检索。
- ~~`project_id IS NULL` legacy 可见性~~ → v0.1 已移除；`fetch_rules` / merger 仅 `global OR project_id = ?`。
- **自动提取** 的规则带 `project_id`（SessionEnd / job 从 `cwd` 解析）。
- README「快速开始」已说明；详见 [project 识别](#project-识别)。

### 改 trigger / 检索字段

- `nokori edit` 支持 `--action` / `--rationale` / `--confidence` / `--status`，以及 `--trigger` / `--variants` / `--search-terms`（会触发 reindex）。
- 仅改措辞可 edit；改触发场景语义也可 dismiss + 重新 `add`。

### Hooks 失败时 fail-open

- 规格错误处理：任何组件失败**降级而非阻塞** Claude Code 会话。
- `hooks/__init__.py` 捕获异常 → `{"continue": true}`，并 `log.exception`。
- **`NOKORI_STRICT=1`**：hook 异常向上抛出，便于调试（默认关闭）。

### Gate 只拦一轮（per user prompt）

- UserPromptSubmit 写 marker；PreToolUse **第一次**匹配 `gate_matcher` 的工具读 marker 后**立即删除**并 block。
- **同一条用户消息**内后续工具全部 pass（含其他 Write）；**下一条用户消息**可再次写 marker。
- 不是「第一个 Write 放行后所有 Write 都不看规则」——是「本轮 marker 已消费」。

### PreToolUse 响应格式

- 阻断仅用 `hookSpecificOutput.permissionDecision: "deny"` 与 `permissionDecisionReason`（[Claude Code Hooks — PreToolUse](https://code.claude.com/docs/en/hooks)）。
- 该事件的顶层 `decision`/`reason` 已弃用；Stop 等其它事件仍用顶层 `decision: "block"`。

### Gate marker 与 prompt_hash

- PreToolUse 无 `prompt` 字段；用 payload 的 `prompt`/`user_prompt`（若有）或本 session 最近一次 `injections.prompt_hash` 与 marker 比对。
- 不一致或无法解析当前 hash → 清 marker、不 block（fail-open）。

### active_sessions/ 目录

- **设计来源**：review G7，登记哪些 Claude session 尚未 SessionEnd。
- **写入**：SessionStart `register`、UserPromptSubmit `touch`、SessionEnd `end`。
- **读取**：`extract.defer_when_active=true` 时用 `count_open_sessions`（`ended_at` 为空）；`nokori status` 区分 `open` 与 `active`（含 idle 窗口）。

### 跨项目 promotion（默认开启，v0.1 不改为手动-only）

- **默认** `[promotion] enabled = true`：正式池与影子池经 `retrieve_formal_and_shadow` **一次**检索后拆分；仅影子侧 HOT 计 `record_shadow_hit`。
- 关闭：`NOKORI_PROMOTION_ENABLED=0` 或 config.toml `enabled = false`（**同时不加载影子池**，场景 C 不可用——显式关 cross-project 学习，不是 bug）。

**为何默认自动提升、无 CLI 确认**

- 目标用户常同时在 2–3 个相关 repo 工作；希望 correction/anti_pattern/solution 在**检索证据**足够时自动变成 global，而不是每次 `nokori promote`。
- 阈值 **3 个不同 `project_id`**（`CROSS_PROJECT_PROMOTE_THRESHOLD`）：降低单项目误触；同日同项目多次 HOT 用 `promotion_evidence` 的 `key` 去重，不刷阈值。
- **`preference` 排除**：偏好强绑定项目语境，跨项目 shadow HOT 不计入、不提升。
- **审查标注**：「v0.1 过早 / 应改手动」→ **不采纳**；若未来要改，应改默认配置或加 `promotion.mode=manual`，而非删现有逻辑。

### 共享 embed server（本地向量）

- **模型只加载一次**：`nokori embed serve` 常驻进程持有一个 `SentenceTransformer`；所有 hook 经 `~/.nokori/embed.sock` 发 `embed` 请求。
- **Hook 路径（不阻塞 Claude）**：`kickstart_server()` — `ping` 失败则 `spawn_server()` **不等待**；当轮检索纯 BM25。`SessionStart` 在 `auto_enabled` 时同样 kickstart，让后续 prompt 更易赶上 ready。
- **CLI / 索引**：`ensure_running(max_wait=15s)` 或默认 45s，可阻塞等待模型加载。
- 单次 hook query 默认 **2s**（`NOKORI_HOOK_EMBED_TIMEOUT`），超时 fail-open → 纯 BM25。
- **`NOKORI_EMBED_ENABLED=1`** 可绕过 ≥20 条阈值；小库也会 ping/spawn server，首条仍可能 BM25-only。
- **怎么关**：
  1. `nokori embed stop`（发 `shutdown` IPC，或 SIGTERM pid）
  2. **空闲退出**：默认 3600s（1 小时）无请求后 server 自退出（`[embed] server_idle_seconds` / `NOKORI_EMBED_SERVER_IDLE`）
  3. 不随单个 Claude session 结束而关（全机共用）
- **CLI**：`nokori embed start|status`；索引/测试优先走 server，server 不可用时可回退进程内 ST（仅 CLI）。

**威胁模型（SEC-4，v0.1 接受）**：

- Unix socket 位于 `NOKORI_DATA_DIR`（默认 `~/.nokori/embed.sock`），面向**本机单用户**；无 IPC 鉴权、`shutdown` 无 token。
- 同 UID 的任意本地进程理论上可连 socket 或发 `shutdown`——与「用户在自己机器上跑 Claude + Nokori」一致，**非**多租户/远程暴露场景。
- **未做**：socket 权限 token、TLS、网络监听。若将 `data_dir` 放在 NFS 或多用户共享目录，风险自负；文档建议保持 `0o700` 数据目录。

### Session 与 async extract 推迟

- **推迟判定**（`extract.defer_when_active=true`）：其它 session 的 `ended_at` 仍为空即算「还开着」，不依赖每条 prompt 更新。
- **默认** `defer_when_active=false`：SessionEnd 立即后台 extract（job 仍会写入）。

---

## 实现取舍（工程向）

### BM25：现场算 IDF + 进程内 LRU

- **现状**：热路径上对当前规则列表现场算 DF/IDF（无 SQLite 持久化索引）；`bm25._cached_index` 以 `(rule.id, updated_at)` 为 key、**LRU 上限 64** 复用已建索引，避免旧版「满 64 整表清空」抖动。
- **依据**：~500 条规则 <100ms（见 `tests/test_search.py`）。
- **后续**：规则上千再考虑离线 IDF / 更大缓存；不阻塞 v0.1。

### Embedding：本地整段单向量；远程保守分块

- **本地（Granite R2）**：默认 **1 块 / 24576 字符**（整段 `_rule_text` 一次 `encode_document`；用户话一次 `encode_query`）。**按字段**判断：`chunk_size` / `chunk_count` 各自未显式设置时用本地默认；只配一项时另一项仍用本地默认（例如只设 `chunk_size=1024` → 1024×1）。空 env（`NOKORI_EMBED_CHUNK_SIZE=`）视为未设置。
- **远程 API**：默认 **4000 × 2**（约 8K 字符/规则，适配 `text-embedding-3-small` 等 8K token 上限）；可在 `[embed]` 覆盖。
- **Query 检索**：`search` / `search_local_shared` 只用 **第一个** query 向量（单 chunk 时即整句）。

### Merger：BM25 预筛 + LLM 判关系

- **现状**：在 project 正式池（candidate/active/trusted）内，用候选规则的 trigger/action/variants/search_terms 拼 query，**BM25 top-20** 送 LLM；若重叠过少（`<5`），用最近 5 条补齐，避免零 token 重叠时完全漏检。
- **依据**：提取是冷路径，全池 BM25 可接受；比「仅 `updated_at` 最近 5 条」更少漏合并。

### Merger：邻居回填（BM25 不足时补最近 5 条）

- **常量**：`MERGE_NEIGHBOR_LIMIT=20`，`MERGE_RECENT_FALLBACK=5`（无 env 开关）。
- **流程**：BM25 邻居 ≥5 → 仅 BM25；&lt;5 → 再按 `updated_at` 补满至少 5 条（上限 20）；池空 → 不调 LLM、直接插入。
- **为何保留回填**：仅 BM25 时，新措辞与旧 trigger **零词重叠** 会得不到邻居，SAME/合并全漏；回填用「最近编辑」兜底，提高 recall。
- **代价**：冷路径多调 LLM、易出现 **E UNRELATED** 与 token 浪费——**v0.1 接受**（产品确认 2026-05-30）。
- **审查标注**：「邻居不足应不调 LLM / 少回填」→ **不采纳**；若改应加 `merge.recent_fallback=0` 类配置，而非默认关。
- **同轮 A+B/D**：对 X 判 A 后对 Y 判 B/D 时，Y `supersede` 到 X（`anchor_id`），不另插第二条 active——见 product-spec §5.2。

### Import 不携带 embedding blob

- Export 仅 rules 表；import 后对 active/trusted **best-effort 重建索引**。
- 大批量导入首次较慢；可后续加 `--skip-embed` 或进度输出。

### Async extract 不设 `NOKORI_EXTRACTING`

- `NOKORI_EXTRACTING=1` **仅**用于 `claude -p` fallback，防止 hook 递归。
- SessionEnd `Popen nokori extract` **必须**能调 LLM；子进程里 pop 该变量（见 `session_end.py`）。

### Embedding（远程 / 本地共享进程）

- 远程：`EmbeddingClient`（OpenAI-compatible）。
- 本地：`nokori embed serve` + Unix socket；hook 与 CLI 经 `embed_ipc` 调用，`embed_server_auto_start` 默认拉起 server。
- 不再在 hook/CLI 热路径进程内加载 `sentence-transformers`（仅 server 进程加载模型）。
- 核心包零依赖；`sentence-transformers` 在 `[local-embed]` optional extra。

### Python 3.11+ 与 `tomllib`

- `requires-python >= 3.11`；`config.toml` 用标准库 `tomllib` 解析，无自研 TOML 解析器。

### rules.db 与程序版本

- 实现用 SQLite `PRAGMA user_version` 标记库格式；**仅空库**一次建表。
- 旧格式或不兼容版本 → `DbError`，用户侧表现为「打不开库」；恢复路径：`nokori export` + 新 `NOKORI_DATA_DIR` / `nokori reset`（**不在用户文档写版本号**）。
- 程序比库新/比库旧时均可能拒绝打开。
- **UserPromptSubmit / `retrieve_and_tier`**：`auto_enabled` 用当次池 `len(formal ∪ shadow_only)`。
- **`index_rule_if_enabled` / SessionStart kickstart**：仍用全库 `total_rule_count()`（`active` + `trusted`）。可能出现全库 ≥20、当前项目池很小 → 后台持续索引，当轮 hook 仍可能纯 BM25；`nokori health` 的 `embed.index` 会 warn。统一阈值留 v0.2。

### Extract job 与 transcript mtime

- SessionEnd 写入 job 时记录 `transcript_mtime`。若之后 transcript 仍被追加（mtime 变化），`nokori extract` **刷新 job 的 mtime/hash 并保留 pending**，不静默删 job，避免永久漏提取。

### Gate：注入宽、阻断窄（v6）

- **注入**：正式池 HOT/WARM/GATE 均可展示，但必须先通过 v6 runtime applicability（required concepts、excluded contexts、dynamic IDF、state permissions）。
- **阻断**：`select_gate_rules` 仅 `trusted` + `severity=gate_eligible`；PreToolUse 对 inspectable tool input 再做 evidence 复核，不匹配则 fail-open 且不消费 marker。
- 普通 `active` reminder/high_risk 规则只注入，不直接阻断工具；`nokori edit --status active|trusted|suppressed` 被拒绝，状态由冷路径、shadow/posthoc 和 lifecycle 控制律维护。

### Fire/shadow/posthoc event lineage（v6）

- `create_fire_event` 是注入记录的唯一写入路径；记录 injected rule version、trigger/action/structured snapshot、decision features、runtime policy、embedding profile、bounded window ref、trigger IDF pool version。
- `SessionEnd` 只入队 posthoc jobs；`nokori maintain` 在冷路径处理 pending posthoc jobs，窗口缺失时标记 `unclear`。
- Shadow events 只用于 candidate/suppressed 的观察与恢复 evidence，不注入正式 context；重复 context 用 fingerprint 去重。

### 影子池与正式池共用检索（一次 pass）

- `retrieve_formal_and_shadow`：`formal ∪ shadow` 调用一次 `retrieve_and_tier`（BM25 + 可选 embedding RRF），再按 `formal_ids` / `shadow_ids` 拆分 HOT/WARM；影子 HOT 同步 `record_shadow_hit`，**不注入**。
- ~~`finally` 内第二次影子检索~~ → 已删除（减少重复 BM25/embed）。
- **v0.1 仍占热路径**：合并后池子略大；promotion 为同步 DB（通常 0–1 条 HOT）。未做「影子 BM25-only」或 SessionEnd 异步计 hit。
- **后续**：若 hook 延迟超标再 profiling；可选影子池降配或延后 promotion。

### Candidate 清理：日历天 TTL（非活跃天）

- **现行**：`lifecycle/maintenance.run_candidate_cleanup` — 自 `created_at` 起 **日历天**（`candidate` 20 天、`anti_pattern` 40 天）；维护任务每 **30 天** 最多跑一次（`maintenance_meta`）。
- **未实现**：`review-history.md` R2-Q2 的「累计 N 个有活跃 session 的天」——需额外计数，留 **v0.2**。
- **对齐**：`README.md` 维护节、`maintenance.py` 注释与本节一致；勿与 dormant 的 `last_hit` 语义混用。

### Unmerge 检查（merged 规则恢复）

- **行为**：`run_unmerge_check`（SessionStart 维护，最多每 **90** 天）：`status=merged` 且 `superseded_by` 非空时，若赢家规则**已删除**，或赢家为 **dormant/archived**，则把 merged 行恢复为 **`dormant`** 并清空 `superseded_by`。
- **原因**：B/D 合并后旧规则进入 `merged`；若新规则被删或长期 dormant，旧规则不应永远卡在 `merged` 不可检索。
- **产品定位**：v0.1 **有意保留**（非规格初稿遗漏）；`product-spec` §6 / §7 与 README 维护节已摘要。审查若要求「merged 终态不可逆」应改产品而非删代码。

### Global promotion 计数

- 阈值：**3 个不同 `project_id`**（与场景 C 一致），非 3 个 `(project, date)` 行。
- 同日同项目重复命中由 `promotion_evidence` 的 `key` 去重。
- DB 列 **`shadow_hit_count`**（非 `cross_project_hits`）= 按「其它项目 × 当天」去重后的 shadow HOT 次数；`nokori status` 显示为 `shadow_hits=`。**升 global** 看 `promotion_evidence` 里**不同 `project_id` 数**（`n/3`），不是 hit 总数。
- **同日同项目**第二次 shadow HOT：`record_shadow_hit` 直接 return，不累加 `shadow_hit_count`（promotion 阈值仍合理）。
- **Shadow 与 candidate 激活**：`shadow_hot` evidence 计入 `evidence_score` / 活跃日；其它项目的 `candidate` 可能被跨项目 shadow 叠到纯 AI 激活条件——v0.1 **有意允许**，非 promotion-only。

### project_id 解析缓存

- `resolve_project_id(cwd)`：`git rev-parse` 结果按规范化 cwd **LRU(64)** 缓存，避免每条 prompt subprocess。

### Merge SAME (A) 对 active/trusted

- ~~规格伪代码：SAME + active/trusted 时新建规则并继承 hit_count~~ → **实现**：对已有行 `add_evidence(..., "same_extraction", 1)`，**不插入**新规则，保留 evidence_log / hit_count / promotion 等完整历史。
- `candidate` 路径仍可能 activate 或叠 evidence，见 [product-spec.md §5.2](./product-spec.md) 与 README「Merge 判定」表。
- **已同步**（2026-05-31）：`product-spec.md`、`technical-design.md`。

### rrf_fuse / tier 不可变

- `ScoredResult` 为 `frozen=True`；`rrf_fuse` 与 `tier_results` 通过 `dataclasses.replace` 写 `rrf_score` / `retrieval_hot`，避免原地修改 BM25 缓存中的对象。

### hot_cache：用 mtime 找上一场（非 session 表）

- **行为**：`find_previous_transcript` 在同目录 `*.jsonl` 中取 mtime **严格小于** 当前文件的最大 mtime；若该文件尚未 extract（`extract_state`），注入最后 **3 条 human**、**≤500 字符**（`HOT_CACHE_*`）。
- **不用 `active_sessions/` 的原因**：SessionStart 已 `register` 当前 session，但 hot cache 不维护「上一 session id → transcript」链；mtime 启发式零 schema、零迁移，对默认 Claude 布局足够。
- **已知误判**：目录内多个并行 session、非相邻文件、外部工具改写 mtime 时，可能注入非预期上一场——**产品接受**；可靠版留 v0.2（registry 或 payload 链）。
- **审查标注**：「应从 session registry 读取」→ **v0.2 候选**，非 v0.1 缺陷。
- **关闭**：`NOKORI_HOT_CACHE=0`。
- 仍只注入 **user** 轮，不注入 assistant/tool——避免未 extract 的 AI 输出污染新 session。

### project_id 与 `git rev-parse`（威胁模型）

- `resolve_project_id(cwd)` 在 hook 给的 `cwd` 下执行 `git rev-parse --show-toplevel`；恶意仓库可通过 `.git/config` 等影响 git 行为——与「在不可信目录里跑 git」同类风险。
- **取舍**：Nokori 假定 Claude Code 的 `cwd` 即用户当前工作区；不做额外 sandbox。仅将路径 hash 为 `project_id`，不执行 git hook 以外的自定义命令。
- **未做**：cwd 白名单 / 禁止非 git 目录——低风险，暂不实现。

### 刻意未做的重构（2026-05-31 review）

| 项 | 决定 |
|----|------|
| `merger.merge_candidate` 拆函数 | 可读性尚可，不拆 |
| `retrieve_and_tier` embed timeout 提取 | 纯风格，不影响行为 |

### extract_state / job 路径

- `extract_state.transcript_path` 与 extract job JSON 统一为 **`Path.resolve()` 字符串**，hot_cache 不再双路径 lookup。

### Extract 单实例锁

- `{data_dir}/extract.lock`（`fcntl` 排他锁）。`nokori extract` 持锁处理全部 pending jobs；并发第二次调用直接退出，避免重复 merge / 重复 LLM。
- **平台**：Unix `fcntl.flock`；Windows `msvcrt.locking`（同一路径 `{data_dir}/extract.lock`）。
- **`--session`** 与批处理 jobs 共用锁，避免并行 extract 重复 merge。

### Export `version` = rules.db `PRAGMA user_version`

- `nokori export` JSON 的 **`version` 字段 = `SCHEMA_VERSION`（当前 2）**，与 `rules.db` 的 `PRAGMA user_version` 一致（不是独立的「JSON 信封版本」）。
- Import 校验 `version == SCHEMA_VERSION`，不匹配则拒绝（需用匹配版本 re-export）。
- 仅 `format=nokori-export` + 按 id 跳过已存在行；**无** v1→v2 库内迁移。

### Gate marker 按 `prompt_hash` 分文件

- 路径：`{data_dir}/gate_markers/{session}/{prompt_hash}.json`（旧版单文件 `pending-ack-*.marker` 只读兼容）。
- **原因**：同一 session 极快连续两条 user message 时，避免后一条覆盖前一条 marker，导致 PreToolUse block 错轮次。

### Injection 历史清理

- `run_injection_cleanup`：默认保留 **30 天** `injections`（扫描间隔 **7 天**）；dismiss 只查 24h，留缓冲。

---

## 配置默认值说明

| 变量 | 代码默认 | 说明 |
|------|----------|------|
| `NOKORI_EMBED_DIMENSIONS` | `0` | `0` = 请求体不传 `dimensions`（由 API/模型默认）。本地模型固定 384。产品文档中的 384 主要指本地 / 显式配置场景。 |

---

## 全维度审查共识（2026-05，故意保留）

> 下列项在并行 code review 中曾被标为「过度设计 / 待改进」。**产品与实现一致：v0.1 保持现状。** 后续审查请先读本节，避免重复讨论。

| ID（审查用语） | 决定 | 文档锚点 |
|----------------|------|----------|
| 跨项目 shadow 自动提升 | 保留，默认开 | 上节「跨项目 promotion」 |
| Hot cache 用 mtime 非 session 表 | 保留 | 上节「hot_cache：用 mtime」 |
| Merge BM25&lt;5 回填最近规则 | 保留 | 上节「Merger：邻居回填」 |
| TOML 配置层 | 保留 | 「Python 3.11+ 与 tomllib」 |
| Windows `extract.lock` | 保留 | 「Extract 单实例锁」 |
| LLM `http_open` / `subprocess_run` 注入 | 保留（测试用） | `tests/test_extract.py` |
| `list_pending` 别名 | 保留 | technical-design §4.3 |
| Export `version` ≠ DB schema | 已对齐 `SCHEMA_VERSION` | 本节 Export |
| Injection 只增不减 | 已加 30 天清理 | 本节 Injection |
| `extract --session` project | job 反查 + `--project` | extract.py |
| Async 重复 spawn | `is_locked` 跳过 | session_end |
| Gate marker 覆盖 | per-hash 文件 | 本节 Gate marker |
| Embedding 半索引 | 文档 + `health embed.index` | README 检索引擎 |
| Unmerge 维护任务 | 保留 | 本节「Unmerge 检查」 |
| 本地 embed server 无 IPC 鉴权 | 单用户可接受 | 本节「共享 embed server」 |
| `log_injection` 单条 API | 已删，仅 `log_injections_batch` | `db.py` |

---

## 相关文档

- [product-spec.md](./product-spec.md) — 功能规格（`docs/`，本地维护，默认不进 Git）
- [technical-design.md](./technical-design.md) — 技术方案（同上）
- [review-history.md](./review-history.md) — 历史审查轮次
