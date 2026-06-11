# 自動提取

[← 返回主文件](../../README.zh-TW.md)

---

關會話後執行，不在互動熱路徑上。設定 LLM 後，Nokori 讀取該場對話的 transcript，提取可能的規則，再讓每條候選走完冷路徑飛輪。

```bash
# 設定 LLM（任何 OpenAI-compatible 端點）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手動提取
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# dry-run 預覽
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 消費所有待處理 job
nokori extract
```

---

## 一條 transcript 怎麼變成規則

冷路徑故意比熱路徑囉嗦。它寧願多判幾輪，也不願把一條含糊規則直接塞進正式池：

1. **讀** transcript，單檔案上限 50MB
2. **壓縮**：使用者消息原樣保留，AI 回覆砍成頭 200 字 + 尾 100 字；整體再壓到約 30k token
3. **提取**：extractor 角色輸出結構化候選
4. **判定 / 重寫 / 再判定**：admission judge 與 final judge 拒絕弱證據/過寬規則
5. **合併規劃**：merge planner 與鄰近規則比較關係
6. **驗證入庫**：歸檔指紋、matcher 編譯、cold-fast-lane 閾值決定存為 candidate 還是 active

**LLM 呼叫格式**：每個角色拆成 system + user 兩條消息。transcript 片段包在 `--- BEGIN UNTRUSTED DATA ---` / `--- END UNTRUSTED DATA ---` 分隔區塊中。

---

## Merge 策略

LLM 給每條候選回一個關係字母 `A`–`E`：

| 判定 | 行為 |
|------|------|
| **SAME (A)** | merge_into_existing / replace / reject |
| **BROADER (B)** | 安全/品質判斷後決定 |
| **NARROWER (C)** | 插入新規則，與已有共存 |
| **CONTRADICTS (D)** | 保守 keep_both 或 reject_new |
| **UNRELATED (E)** | 插一條新 candidate |

失敗處理：

- **提取 LLM 失敗**：job 保持 pending
- **Merge LLM 失敗**：當前候選跳過，job 保持 pending

**鄰居回填**：BM25 預篩不足 5 條時，按 `updated_at` 補上最近更新的規則。

---

## Async Extract Mode

```bash
export NOKORI_EXTRACT_MODE=async
```

| 模式 | 行為 |
|------|------|
| `manual`（預設） | 關會話只落待辦檔案，需手動 `nokori extract` |
| `async` | 關會話時後台直接跑 extract |

日誌：`~/.nokori/logs/async-extract.log`。未設定 LLM（`NOKORI_LLM_BASE_URL` 未設置）時，async 模式會嘗試呼叫本機 `$PATH` 中的 `claude -p` CLI 作為兜底。

邊緣情況：

- `extract.lock` 被佔：不自動啟動，pending job 保留
- Transcript mtime 變了：刷新 job mtime，繼續保留 pending
- 損壞的 job 檔案：挪到 `jobs/bad/`
- `NOKORI_EXTRACT_DEFER_ACTIVE=1`：有其它 open session 時只寫 job 不 fork

---

## Fork 快取提取（僅 Claude Code）

```bash
export NOKORI_EXTRACT_FORK_CACHE=1
```

在 `async` 模式下啟用後，Claude Code session 結束時會 fork 原 session（`claude -r <session-id> --fork-session`）複用 prompt cache 做提取，長對話 input token 成本降低約 90%。

**工作流程：**

1. Session 結束 → `session_end` hook 偵測到 `Host.CLAUDE`
2. 背景啟動 `fork_runner`
3. 檢查 byte offset：若之前已部分提取，讀取 offset 前第 3 條 user message 作為錨點，告知模型只提取錨點之後的新內容
4. 壓縮偵測：若 offset 之後存在 `compact_boundary`（上下文已被壓縮），跳過 fork 回退到正常讀 transcript 原文的路徑
5. Fork session，prompt 帶角色覆寫指令強制執行提取行為
6. 解析 JSON 輸出 → 冷管道（admission → rewrite → merge → insert）

**前置條件：**

- `claude` CLI 在 `$PATH` 中
- `extract.mode = "async"`
- `extract.fork_cache = true`
- 僅 Claude Code session 生效（Cursor session 始終走正常路徑）

**回退：** CLI 不可用、session ID 無效、fork 逾時（300s）、輸出非法 JSON 時，自動回退到正常的 `nokori extract` async 路徑。

日誌：`~/.nokori/logs/fork-extract.log`
