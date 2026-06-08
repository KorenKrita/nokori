# 架構詳解

[← 返回主文件](../../README.zh-TW.md)

---

## 自治品質飛輪

Nokori 的核心是 autonomous quality flywheel（自治品質飛輪）：每條 rule（規則）都要先證明自己，才能從 memory（記憶）變成 behavior（行為）。

這個循環刻意分成三段：

- **Cold path（冷路徑）**：關會話後，多角色 LLM 流水線負責提取、判定、重寫、合併與評測候選規則。弱規則擋在門外，太寬的規則收窄，不安全的合併會被拒絕或拆分。
- **Hot path（熱路徑）**：聊天時，hook 只做確定性的檢索、匹配、打分、標記讀寫與 fail-open（失敗放行）。你的 prompt 和 Agent 回覆之間沒有 LLM 等待。
- **Evidence loop（證據回流）**：HOT/WARM 注入會產生 fire events（觸發事件）；candidate/suppressed 的影子命中會產生反事實證據；maintenance（維護任務）根據評估後的 evidence（證據）執行生命週期遷移。

讓這個循環真正有用的是：

- **Structured triggers（結構化觸發器）**：concepts（概念）、required concept groups（必需概念組）、trigger variants（觸發變體）、excluded contexts（排除上下文）、tool tags（工具標籤）、severity（嚴重度）、source origin（來源）、runtime policy version（執行時策略版本）與 lineage metadata（譜系元資料），而不是幾段鬆散文本。
- **Autonomous lifecycle（自治生命週期）**：`candidate → active → trusted`，也支援 `suppressed` 恢復和終態 `archived`。手動指令可以 archive（歸檔），但不能偽造 trust（信任）。
- **Conservative Gate（保守門閘）**：Gate 是給 `trusted + gate_eligible` 規則的一次性提醒煞車，不是權限系統。
- **Hybrid retrieval（混合檢索）**：BM25 永遠可用；可選 remote embedding（遠端向量）或本地 Granite multilingual model 補語義召回；RRF 與 runtime applicability（適用性判斷）決定 HOT/WARM。
- **本地優先**：SQLite、hook 日誌、job 佇列、Gate marker、embedding 權重、Web UI 狀態都在 `~/.nokori/` 下。遠端 LLM / embedding 端點按需啟用。
- **跨工具可觀測**：Claude Code 與 Cursor 都支援；`nokori test`、`status`、`health`、`logs`、`extract`、`maintain` 與 Web UI 能解釋規則為什麼觸發、為什麼沒觸發。

Nokori 最重要的承諾是 restraint（克制）：它可以早早 reminder（提醒），但必須攢夠 evidence（證據）才有資格變得強勢；開始幫忙之後，也要繼續接受 evidence review（證據審查）。

---

## Hook 時序

Nokori 在 Claude Code（與 Cursor）裡掛了 **4 個 hook**。你正常聊天時，它們只在本地查庫、算分、讀寫小檔案——**hook 裡絕不呼叫 LLM**，否則每條消息都會因等待模型而阻塞。

| Hook | 它做什麼 | 延遲預算 |
|------|---------|----------|
| `SessionStart` | 會話開始：可選注入上一場沒提取過的 user 片段，並觸發資料庫維護 | ≤ 1.5s |
| `UserPromptSubmit` | 每次發消息：檢索規則 → 注入上下文 → 必要時寫下 Gate 標記 | ≤ 500ms |
| `PreToolUse` | 工具呼叫前：若有標記就**攔一次**，隨後清除標記 | ≤ 50ms |
| `SessionEnd` | 關會話：記一個「待提取」任務檔案，async 模式下可後台跑 extract | ≤ 200ms |

落到實處就兩件事：

1. **提醒（注入）**——命中的規矩按 HOT/WARM 檔位寫進 `additionalContext`，Claude 回覆前就看得見
2. **攔一次（Gate）**——只有 `trusted` 且 `severity=gate_eligible`、prompt 證據夠強、工具輸入證據也過關的規則才會攔工具；普通 active 只提醒

---

## 注入 vs 阻斷

| | 注入（`additionalContext`） | Gate（PreToolUse deny） |
|--|------------------------------|-------------------------|
| 規則範圍 | 正式池 HOT + WARM | 正式池 HOT 的子集 |
| 狀態 | `active` 與 `trusted` | 僅 `trusted` |
| 嚴重度 | `reminder`、`high_risk`、`gate_eligible` | 僅 `gate_eligible` |
| 其它條件 | required concepts、excluded contexts、動態 trigger 證據、選擇預算都過關 | 還要強 prompt 證據、當前 runtime policy、prompt hash 對得上；工具輸入可檢查時還要 tool-input 證據 |

Gate 不是權限系統，而是一腳只踩一次的提醒煞車：展示相關規則、拒絕一次、清除 marker，同一條消息裡的後續工具呼叫繼續放行。

---

## Shadow Pool（影子池）

每次 `UserPromptSubmit`，Nokori 都分開檢索**正式池**和**影子池**，防止影子證據搶走真實提醒的 HOT/WARM 預算。

- **正式池**：`active` + `trusted`；只有這個池能注入
- **影子池**：`candidate` + `suppressed`；永不注入，永不 Gate
- Candidate shadow matches 會變成 candidate → active 的反事實證據
- Suppressed shadow matches 會變成 suppressed → active 的恢復證據

---

## 熱快取

SessionStart 要找「上一場 transcript」，兩步走：

1. **優先**讀 `{data_dir}/transcript_index/` 裡 SessionEnd 寫下的 previous/current 指標
2. **回退**：同目錄下 mtime 嚴格早於當前檔案的最新那個 `*.jsonl`

若上一場尚未 extract，則從檔案**尾部**注入最後 3 條 user 消息（500 字元，預算獨立於規則的 1500 字元上限）。

---

## 術語速查

| 詞 | 說明 |
|----|------|
| **hook** | Claude Code / Cursor 在固定時機自動執行的一小段指令 |
| **injection**（注入） | 把匹配到的規矩寫進 Agent 當輪能看到的上下文裡 |
| **Gate**（門閘） | 對 `trusted` + `gate_eligible` 的規矩：第一次匹配的工具呼叫先 deny 一次 |
| **marker**（標記） | 本輪「請先讀 Gate 規則」的臨時標記，用一次即清除 |
| **transcript** | 整場對話的 `.jsonl` 日誌 |
| **trigger / action** | 規矩的兩半：「什麼情況下」+「應該怎麼做」 |
| **short_id** | 規矩的短編號（如 `a3f2b1`） |
| **dismiss** | 退役一條規矩 |
| **HOT / WARM** | 匹配程度的檔位：很相關 / 有點相關 |
| **BM25** | 按關鍵詞重疊打分，零 GPU、預設就有 |
| **embedding** | 按語義相似度打分；可選開啟 |
| **RRF** | 把 BM25 榜和向量榜合併成一張總榜的演算法 |
| **fail-open** | Nokori 自己出錯時不阻斷 Claude |
| **extract** | 從 transcript 裡用 LLM 提取候選規則 |
| **shadow pool** | 後台匹配 candidate/suppressed 規則：只記證據，不注入 |
| **OpenAI-compatible** | API 位址填 `.../v1` 就能接 Ollama、LM Studio、OpenRouter 等 |
