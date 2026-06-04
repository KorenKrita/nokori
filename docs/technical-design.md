# Nokori 技术设计（v0.1）

> 与 [product-spec.md](./product-spec.md) 配套。实现以仓库 `nokori/` 包为准。

---

## 1. 架构概览

```
Claude Code hooks (stdin JSON → stdout JSON)
    ├── session_start   → hot_cache, maintenance, embed kickstart
    ├── user_prompt_submit → retrieve_formal_and_shadow, inject, gate marker
    ├── pre_tool_use    → marker + prompt_hash → deny once
    └── session_end     → extract job file

CLI (nokori *)          → 同一 Db / Config / search 栈
Cold path (extract)     → reader → compressor → extractor → merger (+ LLM)
```

- **存储**：SQLite `rules.db`（内部 `PRAGMA user_version` 标记格式，用户无感）。
- **依赖**：核心包 stdlib only；`[local-embed]` 可选 `sentence-transformers`。
- **热路径零 LLM**；LLM 仅 extract/merge 与可选远程 embedding。

---

## 2. 检索管线

### 2.1 `retrieve_and_tier`

1. `bm25.search(prompt, rules, top_k)`
2. 若 `embedding.auto_enabled(cfg, pool_size)`：
   - 本地：`search_local_shared` + `embed_ipc`（hook 短超时）
   - 远程：`EmbeddingClient.search`
3. `ranker.rrf_fuse` → `ranker.tier_results`
4. 返回 `RetrievalResult(hot, warm, bm25_matches, embed_mode)`

`pool_size` 默认 `len(rules)`（当次池），非 `total_rule_count()`。

### 2.2 `retrieve_formal_and_shadow`

```text
formal_ids = {r.id for r in formal_rules}
shadow_only = [r for r in shadow_rules if r.id not in formal_ids]
combined = formal_rules + shadow_only
shadow_ids = {r.id for r in shadow_only}   # 与 combined 一致，防重叠双计

result = retrieve_and_tier(prompt, combined, ...)
formal_hot/warm = filter by formal_ids
shadow_hot = [r for r in result.hot if r.rule.id in shadow_ids]
```

Hook：`user_prompt_submit` 对 `shadow_hot` 调用 `promotion.record_shadow_hit`；对 formal hot/warm 调用 `log_injection`。

### 2.3 `ranker`

- **RRF** `K=60`；`ScoredResult` 为 **`frozen=True`**，fuse/tier 用 `dataclasses.replace` 写 `rrf_score` / `retrieval_hot`。
- **`tier_results`**：top-5 + `meets_min_evidence` + top1 显著性 → HOT/WARM。

### 2.4 BM25 与 embedding 文档字段（检索信号）

| 路径 | 纳入字段 |
|------|----------|
| BM25 `_rule_doc_tokens` | `trigger_text`、`trigger_variants`、`search_terms` |
| Embedding `_rule_text` | 上列 + **`action`** + **`rationale`** |

用户 prompt 若主要与 **action** 用词重叠、trigger 很抽象：**未启用 embedding** 时可能 BM25 零命中；启用后 RRF 可部分弥补。v0.1 **不**把 `action` 全文并入 BM25（噪声与 token 权衡）；取舍见 [design-decisions.md](./design-decisions.md)。

### 2.5 BM25 缓存

- 进程内 LRU（64）键 `(rule.id, updated_at)`；无 SQLite 倒排索引。

---

## 3. Gate 实现

| 模块 | 职责 |
|------|------|
| `gate/marker.py` | 读写 `gate_markers/{session}/{prompt_hash}.json`；`delete_session` 会删遗留 `pending-ack-*.marker`（不再读取）；`prompt_hash` SHA256 前 16 hex |
| `gate/blocker.py` | `format_injection`（全 HOT/WARM）/ `select_gate_rules`（仅 correction+anti_pattern HOT） |
| `hooks/pre_tool_use.py` | matcher → DB → `resolve_current_prompt_hash` → `prompt_hash_matches` |

**prompt_hash 解析**（`resolve_current_prompt_hash`）：

1. `payload["prompt"]` / `payload["user_prompt"]`
2. `SELECT prompt_hash FROM injections WHERE session_id=? ORDER BY created_at DESC LIMIT 1`

无法解析当前 hash 时：**清空 session markers 并放行**（不用磁盘上最新 marker 顶替）。**不匹配**：`prompt_hash_matches` → False → 删 marker，`continue: true`（spec §4.2）。

---

## 4. Extract / Merger

### 4.1 邻居检索

- 在 `global OR project_id` 池内对候选拼 query，BM25 top-20；BM25 命中 **&lt; 5** 时按 `updated_at` 回填至至少 5 条（上限 20）。**故意保留**，取舍见 [design-decisions.md](./design-decisions.md) 与 product-spec §5.3。

### 4.2 `merge_candidate`

- LLM 返回 `relationships[]`，每项 `existing_id` + `judgment`（A–E）；同一 `existing_id` 只处理一次（`handled_existing`）。
- **A + candidate**：evidence / activate（见 product-spec §5.2）。
- **A/overlap with active/trusted/suppressed**：LLM merge planner 输出 relation/safety/quality 后，必须经过 deterministic `apply_merge_policy`；不得绕过 policy 直接写状态。
- **B/D**：`supersede` 到 `pending_new`（若已有）、否则 `anchor_id`（若本轮已有第一个 A）、否则 `_persist_new` 再 supersede。
- `merge_ok=False` 时不写 extract_state done。

### 4.3 Jobs

- `extract/jobs.py`：`write_job`（同 hash 可 merge 更新 `project_id`）；`list_jobs(cfg, status="pending")`。
- `extract.lock`：`fcntl` 排他锁，防并行 merge；已有实例时 CLI **exit 2**。

---

## 5. Embedding 子系统

| 组件 | 说明 |
|------|------|
| `embedding_server.py` | 常驻进程，单模型 |
| `embed_ipc.py` | Unix socket JSON-line；响应读取上限 **1 MiB** |
| `embedding.py` | 远程 API / 本地 shared；`kickstart_server` vs `ensure_running` |

Hook 默认 `embed_hook_timeout_seconds`（如 2s）；超时 → 空 embed 结果，纯 BM25。

---

## 6. 数据库

- `Db.transaction()`：`BEGIN IMMEDIATE`；**禁止嵌套**（`_in_tx` → `DbError`）。
- `log_injection`：仅 `level=hot` 更新 `hit_count` / `last_hit`。
- v6 不再有 dormant 热路径再激活；active/trusted/suppressed 转换由 posthoc/shadow lifecycle 控制律处理。
- 仅空库初始化；格式/版本不匹配 → `DbError`（对用户：export 或新数据目录）。

---

## 7. Import / Export

- Export：`format=nokori-export`，无 embedding blob。
- Import：字段长度上限（`trigger_text` 16KiB、`action` 8KiB 等）；超限 `NokoriError`。
- Import 后对 active/trusted `index_rule_if_enabled`。

---

## 8. 配置

- `config.py`：`Config.from_env()` + `config.toml`；`ensure_dirs()` → `mkdir(..., mode=0o700)` + `chmod(0o700)`。
- LLM：`{llm_base_url.rstrip('/')}/chat/completions`（**不在代码里追加 `/v1`**，由配置提供）。

---

## 9. Project ID

```text
resolve_project_id(cwd)
  → git rev-parse --show-toplevel (cwd=规范化路径, timeout=2s)
  → sha256(resolved_root)[:8] → "{dirname}-{hash}"
```

- LRU(64) 缓存；非 git 用 cwd 路径 hash。
- **威胁模型**：恶意 `.git` 与在不可信目录执行 `git` 同类；见 design-decisions。不实现 sandbox。

---

## 10. 测试锚点

| 领域 | 测试文件 |
|------|----------|
| 检索 / shadow 重叠 | `tests/test_retrieve.py`, `tests/test_retrieve_shadow_overlap.py` |
| tier / BM25 | `tests/test_search.py` |
| merge / extract | `tests/test_extract.py` |
| import 上限 | `tests/test_export_import.py` |
| DB 嵌套事务 | `tests/test_db.py` |
| DB 初始化 / 不兼容库 | `tests/test_db_schema.py` |

---

## 11. 刻意未拆/未抽（2026-05-31）

- `merger.merge_candidate` 单函数 ~80 行：可读性可接受。
- `retrieve_and_tier` 内 local/remote timeout 分支未提取 helper。
