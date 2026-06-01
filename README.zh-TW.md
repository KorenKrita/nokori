# Nokori (殘り)

**Languages:** [English](README.md) | [简体中文](README.zh-CN.md) | **繁體中文** | [日本語](README.ja.md)

> 經驗留下的痕跡，比記憶更深。

**面向 Claude Code 的規則筆記本**——把你糾正過的話、踩過的坑，沉澱爲下次能自動召回的行爲規則。

記錄的不是「上次聊了什麼」，而是「下次該怎麼做」：在相似場景裏先提醒 Claude，必要時**攔截一次工具調用**，讓它先看到規則再改代碼。

---

## 它適合誰？

- 總在糾正同一類問題（強推、忘跑遷移、危險命令）的人  
- 希望**跨項目**積累「別這麼幹」而不是每個 repo 重來一遍的人  
- 接受「規則存在本地 SQLite、可導出」，不想把整段聊天再發給 LLM 的人  

---

## 一分鐘瞭解

```
你糾正 Claude
    → Nokori 記下一條規矩（觸發場景 + 該怎麼做）
    → 下次你的話有點像當時
    → 自動寫入 Claude 的上下文（提醒）
    → 若是高危糾正類且命中很準：第一次改文件/跑命令前先攔截一次（Gate）
```

**聊天時** Nokori 儘量快（檢索 + 文件，不調 LLM）；**關會話後** 才用 LLM 從 transcript（會話記錄）裏挖新規矩。

---

## 術語速查

第一次看文檔若碰到英文縮寫，可先掃這張表；後文仍會重複關鍵概念。

| 詞 | 說明 |
|----|------|
| **hook** | Claude Code 在固定時機自動執行的一小段命令（如每次發送消息前後） |
| **injection**（注入） | 把匹配到的規矩寫進 Claude 當輪能看到的上下文裏 |
| **Gate**（門閘） | 對少數「高危糾正」類規矩：第一次匹配的工具調用先 **deny**（拒絕）一次，逼 Claude 讀規矩 |
| **marker**（標記） | 本輪「請先讀 Gate 規則」的臨時標記，用一次即清除 |
| **transcript** | Claude 整場對話的 `.jsonl` 日誌，自動提取規矩時讀它 |
| **trigger / action** | 規矩的兩半：「什麼情況下」+「應該怎麼做」 |
| **short_id** | 規矩的短編號（如 `a3f2b1`），用來 dismiss 或對照 |
| **dismiss** | 退役一條規矩（不再檢索、不再 Gate） |
| **HOT / WARM** | 匹配程度的檔位：很相關 / 有點相關；越熱字越多 |
| **BM25** | 按關鍵詞重疊打分，零 GPU、默認就有 |
| **embedding**（嵌入向量） | 按語義相似度打分；規則多了以後可選開啓 |
| **RRF** | 把 BM25 榜和向量榜合併成一張總榜的算法 |
| **fail-open** | Nokori 自己出錯時**不卡死** Claude，寧可這輪不提醒 |
| **extract** | 從 transcript 裏用 LLM **提取**候選規矩（冷路徑，不急） |
| **shadow pool**（影子池） | 別的項目裏的規矩：只用來統計「是否該升全局」，**不注入到你當前對話** |
| **promotion**（晉升） | 一條項目規矩被多個別的項目認可後，升爲 **global**（全局可見） |
| **candidate / active / dormant** | 待確認 → 正在用 → 很久沒用先休眠 |
| **merged / archived** | 被新規矩取代 / 你或系統作廢 |
| **supersede** | 新規矩頂替舊規矩（舊的狀態變 merged） |
| **OpenAI-compatible** | API 地址填 `.../v1` 就能接 Ollama、LM Studio、OpenRouter 等 |

---

## 工作原理

Nokori 在 Claude Code 裏掛了 **4 個 hook**；你正常聊天時，它們只在本地查庫、算分、讀寫小文件——**不在 hook 裏調 LLM**（否則每次發消息都要等模型，受不了）。

| Hook | 人話 | 延遲預算 |
|------|------|----------|
| `SessionStart` | 會話開始：可選注入上一場未提取的 user 片段 + 觸發數據庫維護 | ≤ 1.5s |
| `UserPromptSubmit` | 每次發送消息：檢索規則 → 注入上下文 → 必要時寫入 Gate 標記 | ≤ 500ms |
| `PreToolUse` | 工具調用前：若有標記則 **攔截一次**，隨後清除標記 | ≤ 50ms |
| `SessionEnd` | 關會話：記一個「待提取」任務文件，async 模式可後臺跑 extract | ≤ 200ms |

兩件核心事：

1. **提醒（注入）** — 命中規矩按 HOT/WARM 寫進 `additionalContext`，Claude 回覆前就能看到  
2. **攔截一次（Gate）** — 僅 **糾正 / 反模式** 且命中準確、高置信、處於 active 的規則會攔截工具；**solution（解法類）只提醒不攔截**（見 [注入 vs 阻斷](#注入-vs-阻斷)）

---

## 安裝

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
pip install -e .

# 可選：本地 embedding（會安裝 sentence-transformers，並自動下載模型權重到 ~/.nokori/models/）
pip install -e ".[local-embed]"

# 註冊 hooks 到 Claude Code（已裝 [local-embed] 時也會 prefetch，與 hooks 是否變更無關）
nokori install
# 跳過權重下載：nokori install --no-prefetch-embed
# 手動補下/重試：nokori embed prefetch

# 驗證
nokori health
nokori status
nokori logs          # hook / pipeline / async-extract 日誌
```

`nokori install` 會把上述 hook 寫進 `~/.claude/settings.json`，**合併**進去，不會蓋掉你已有的別的插件。若 `settings.json` 已損壞（非合法 JSON），install **拒絕寫入**並退出（與 `nokori health` 對 settings 的校驗一致）。

```bash
# 預覽將要寫入的變更
nokori install --dry-run

# 卸載（只移除 nokori 的 hooks，保留其他）
nokori install --uninstall

# 臨時禁用（hooks 保留但不執行）
nokori install --disable
nokori install --enable
```

---

## 快速開始

下面三步夠你感受 Nokori；細節在後面章節。

### 1. 手動添加一條規則

```bash
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --rationale "force push overwrites peers' work" \
  --source-type correction \
  --confidence high \
  --variants "git push --force,git push -f" \
  --terms-zh "強推,覆蓋代碼"
```

不傳 `--project-id` 時寫入 `project_scope=global`（所有項目正式池可見）。傳了則 `project_scope=project` 並綁定該 `project_id`。

### 2. 模擬檢索（不啓動 Claude 也能試）

```bash
nokori test "I'll just git push --force this branch"
# 默認 project_id = 當前目錄 git 根（與 hook 一致）；可用 --project 覆蓋
```

輸出：

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

### 3. 在真實 session 裏試用

照常開 Claude Code 寫代碼即可。當你的話和某條規矩比較像時：

- Claude **回覆前**會看到注入的規矩（HOT 寫得多，WARM 寫得短）  
- 若是 **糾正 / 反模式** 且命中特別準：第一次點 Write / Bash 等可能被 **攔一下**，界面裏會看到原因和 `short_id`  
- **同一條消息內**，攔截過一次後，後續工具調用會放行（標記已清除）  
- **解法類（solution）** 規則：可出現在提示裏，但**不會攔截工具**

### 4. 規則過時了？（Dismiss）

每條規則有一個 **short_id**（如 `a3f2b1`），在注入文案和 Gate 阻斷理由裏都會出現。規則若已不適用，應**退役**（狀態變爲 `archived`，不再檢索、不再 Gate）。

**方式一：終端（隨時可用）**

```bash
nokori dismiss a3f2b1
```

**方式二：在對話裏說一句話（配合 Gate / 注入提示）**

當某條規則剛被注入，或 Claude 被 Gate 攔住時，提示裏會寫：可以說 `dismiss <short_id>` 來退役。你在**下一條用戶消息**裏寫：

```text
dismiss a3f2b1
```

`UserPromptSubmit` hook 會識別並歸檔該規則。

| 對比 | CLI `nokori dismiss` | 對話裏 `dismiss <short_id>` |
|------|----------------------|-----------------------------|
| 時間限制 | **過去 24 小時內** 曾被注入過（任意 session） | **過去 24 小時內** 注入過；正常 `session_id` 限當前 session，`session_id` 爲 `-` 時與 CLI 相同（任意 session） |
| 動詞 | 固定子命令 | 可配置，見 `dismiss_phrase`（默認 `dismiss`） |

若把 `dismiss_phrase` 改成 `forget`，對話裏應寫 `forget a3f2b1`（`nokori dismiss` 子命令名不變）。格式固定爲：**一個單詞 + 空格 + short_id**，不是整段自然語言。

配置：`dismiss_phrase` / `NOKORI_DISMISS_PHRASE`，見 [配置文件](#配置文件) 與 [config.toml.example](config.toml.example)。

---

## Gate 與 PreToolUse：兩層「工具匹配」

> **Gate 是什麼？** 不是全程禁用工具，而是「本輪第一次調用敏感工具前，先讓 Claude 看到相關規則」。攔截一次後清除標記，同一條消息內後續工具照常執行。

很多人以爲只有一個「Gate 攔截工具」開關，其實是**兩層**，配置位置和內容都不同：

```
Claude 準備調用工具
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第一層：Claude Code settings.json 的 PreToolUse.matcher │
│ 「要不要執行 nokori hook pre-tool-use」                    │
│ 默認：Edit|Write|MultiEdit|Bash|NotebookEdit            │
│ Read / Grep 等默認不會進 hook                            │
└─────────────────────────────────────────────────────────┘
    │ hook 已執行
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第二層：Nokori [gate].matcher（NOKORI_GATE_MATCHER）       │
│ 「hook 裏要不要對這次 tool_name 做 block」               │
│ 默認：同上；須爲 Python 正則，對 payload.tool_name fullmatch│
└─────────────────────────────────────────────────────────┘
    │ 有 marker 且匹配
    ▼
  deny 一次 → 刪 marker → 重試同工具則放行
```

Gate 阻斷時 hook 返回 Claude Code 官方格式（[Hooks reference — PreToolUse](https://code.claude.com/docs/en/hooks)）：`hookSpecificOutput.permissionDecision: "deny"` 與 `permissionDecisionReason`（展示給 Claude）。頂層 `decision`/`reason` 對該事件已棄用，Nokori 不再輸出。

### 第一層：讓 hook 在哪些工具上運行

- **配置文件**：`~/.claude/settings.json`（`nokori install` 寫入，不會讀 `config.toml`）
- **字段**：`hooks.PreToolUse` 裏 nokori 那條的 `matcher`
- **默認值**（install 時）：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成「任意工具都跑 hook」**：把該條的 `matcher` 改爲 `*`（Claude Code 約定，表示所有 PreToolUse 事件）

示例（僅示意 nokori 那條，保留你其它 hooks）：

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

已安裝過的話需**手動改** settings，或 `nokori install --uninstall` 後再 `install`（會按倉庫內默認 matcher 寫回，不是 `*`）。改完後無需改 `config.toml`。

### 第二層：hook 內對哪些 tool_name 真正 block

- **配置文件**：`~/.nokori/config.toml` 的 `[gate] matcher`，或環境變量 `NOKORI_GATE_MATCHER`
- **含義**：hook 已被調用時，用 **Python `re.fullmatch`** 匹配 payload 裏的 `tool_name`
- **默認值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成「凡進 hook 的工具都參與 block 判斷」**：設爲 `.*`（**不要**寫字面量 `*`，在正則裏非法）

```toml
[gate]
matcher = ".*"
```

僅改這一層、不改 settings 時：Read 等仍**不會**進 hook，自然也不會被 block。兩層要一起改才能達到「任意工具都可能被 Gate」。

### 注入 vs 阻斷

| | 注入（`additionalContext`） | Gate（PreToolUse deny） |
|--|------------------------------|-------------------------|
| 規則範圍 | 正式池 HOT + WARM | 正式池 HOT 的子集 |
| `source_type` | 全部（含 solution、preference） | 僅 **correction**、**anti_pattern** |
| 其它條件 | 檢索分層達標 | 且 **high** + **active** |

例如 `solution` 規則可以出現在 HOT 提示裏，但**不會**因爲 Gate 攔住你的第一次 Write/Bash。

### 其它 Gate 相關配置

| 項 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 總開關；關則只注入、不 block |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | marker 有效期（默認 600s），過期不再 block；**設爲 `0` 表示永不過期** |

**Prompt-hash 不匹配（fail-open）**：`UserPromptSubmit` 寫入 marker 時記錄當前 prompt 的 hash；`PreToolUse` 用 payload 或本 session 最近一條 `injections.prompt_hash` 解析當前 hash（**不會**用磁盤上「最新 marker 文件」冒充當前輪）。若無法解析或與 marker 不一致（用戶已發下一條消息），**刪除 marker 並放行工具**，不 block。

---

## 自動提取

會話結束後的後臺任務：配置好 LLM 後，Nokori 讀取 Claude Code 的 **transcript**（`.jsonl` 會話記錄），將糾正總結爲候選規則，再與庫中已有規則合併。

```bash
# 配置 LLM（任何 OpenAI-compatible 端點）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手動提取（指定 transcript；project 優先用 SessionEnd job 裏記錄的 project_id）
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# 或 dry-run 預覽
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 消費所有待處理的 extract jobs
nokori extract
```

提取流程：讀 transcript（單文件 ≤ 50MB）→ 壓縮（保留用戶消息，截斷 AI 響應）→ LLM 提取候選規則 → 與已有規則合併（SAME/BROADER/CONTRADICTS/UNRELATED）。

**LLM 調用方式**：提取與 merge 使用 **system**（固定指令）+ **user**（不可信正文）兩條消息；transcript / 候選 / 已有規則正文包在 `--- BEGIN UNTRUSTED DATA ---` 分隔塊內，降低工具輸出裏夾帶的對抗指令影響。遠程端點爲 OpenAI-compatible `/v1/chat/completions`；未配置時 fallback 爲 `claude -p`（system 進 `--system-prompt`，正文進 stdin）。

**Merge 判定（實現）** — LLM 關係字母 `A`–`E` 對應 SAME / BROADER / NARROWER / CONTRADICTS / UNRELATED：

| 判定 | 行爲 |
|------|------|
| **SAME (A)** + 已有 `candidate` | 加 evidence；high correction 可立即 activate，否則按 evidence 規則激活 |
| **SAME (A)** + 已有 `active` / `dormant` | **不新建規則**；對已有行 `add_evidence(..., "same_extraction", 1)`，保留全部歷史 |
| **BROADER / CONTRADICTS (B/D)** | 插入新規則並 `supersede` 舊規則；若同輪已對另一條判 **A**，則 `supersede` 到 A 那條，不另插第二條 active |
| **NARROWER (C)** | 插入新規則（與已有規則共存）；若同輪還有 **SAME (A)**，仍會插入本條候選 |
| **UNRELATED (E)** | 插入新 `candidate`，與鄰居獨立 |
| 無強關係 | 插入新 `candidate` |

**Merge LLM 失敗**（鄰近規則存在但關係 JSON 無效/超時）：**當前候選**仍會作爲獨立規則插入，但 `merge_ok=false`，`nokori extract` **不**標記 transcript 已提取，job **保持 pending**（checkpoint 保留已處理候選）以便重試。

**提取 LLM 失敗**（或非 JSON）：**不會插入**候選；job **保持 pending**。

**鄰居回填（v0.1 故意保留）**：BM25 預篩不足 5 條時，會再塞入按 `updated_at` 最近的規則再送 LLM，可能多耗 token、出現大量 UNRELATED——用於減少「零詞重疊」漏合併；無開關。取捨：寧可多調 LLM，也不漏掉應合併的 SAME/B/D。

沒有配置 LLM 時，Nokori 會嘗試 `claude -p --model haiku` 作爲 fallback（prompt 經 stdin，不進 argv）。

---

## 數據庫

- SQLite `rules.db`，首次使用時自動創建
- 若數據庫與當前 nokori 版本不兼容，會報錯；請先 `nokori export` 備份，或換新 `NOKORI_DATA_DIR` / `nokori reset`

## 規則生命週期

> 狀態名是英文，含義見 [術語速查](#術語速查)。下面這張表給想細調的人看。

```
candidate（待確認）→ active（在用）→ dormant（休眠）→ 可再激活或 archived（作廢）
                              ↘ merged（被新規矩取代）
```

| 狀態 | 會參與提醒嗎？ | 會 Gate 嗎？ | 怎麼來的 |
|------|----------------|--------------|----------|
| `candidate` | 否 | 否 | 自動提取、置信度一般，先觀察 |
| `active` | 是 | HOT 且類型對時可能 | 你手動 high 糾正，或證據夠了自動升 |
| `dormant` | 是，但命中時最多 WARM | 否 | 30 天沒被「強相關」用到（見 `last_hit`） |
| `merged` | 否 | 否 | 被更新的規矩取代 |
| `archived` | 否 | 否 | 你 dismiss，或 candidate 放太久被清理 |

### 激活條件

- **手動 `nokori add`** 或 **提取合併時**：`high` + `correction` 候選 → 直接 `active`（含初始 `user_correction` 證據）
- 純 AI evidence（含跨項目 `shadow_hot`）：`evidence_score >= 2` 且跨 `>= 2` 個活躍天

**`last_hit` 語義**：用於 dormant 掃描（`last_hit` 缺失時用 `created_at`）。在以下情況更新：**(1)** 正式池 HOT/WARM **實際寫入上下文**的注入；**(2)** dormant 規則檢索達標、當輪再激活。`hit_count` 仍僅 HOT 注入 +1。

**Dormant 再激活**：檢索分達 HOT 檔時，**當輪**仍按 WARM 注入（無 gate）；DB **當輪**即 `status=active` 並更新 `last_hit`，**下一輪**可 HOT + gate（若類型爲 correction/anti_pattern）。與 `UserPromptSubmit` hook 行爲一致。

### Project ID

Nokori 通過 `git rev-parse --show-toplevel` 解析項目根目錄，生成 `<目錄名>-<路徑hash前8位>` 作爲 project_id。不同路徑的同名倉庫不會衝突。非 git 目錄 fallback 爲 cwd 路徑 hash。

### Global Promotion

每次 `UserPromptSubmit` 對**正式池 ∪ 影子池**做一次檢索（BM25 + 可選 embedding RRF），再按池拆分：僅正式池 HOT/WARM 注入；影子池 **HOT 與 WARM** 均計 `record_shadow_hit`（僅 promotion，不注入當前對話）。**≥3 個不同 project_id** 命中後升爲 `global`（**無二次確認**，v0.1 產品選擇）。`preference` 不參與。

### Shadow Pool（影子池）

**簡述**：你在項目 A 寫代碼時，項目 B 中已驗證的規則也會參與**打分**，但**不會注入 A 的對話**——僅用於判斷「該規則是否應升爲全局」。

- 和當前項目規矩用同一套檢索（BM25，規則夠多時還有 embedding + RRF）  
- 算到 **HOT 或 WARM** 都會記一次「影子命中」（promotion 證據）  
- **每個「別的項目 × 當天」最多記 1 次**（同一天同一項目重複命中不刷分）  
- **≥3 個不同項目**都命中過 → 規矩升爲 `global`（全局），不用你點確認  

新項目一個規矩都沒有時，只要開了 promotion，影子池仍會跑——方便從零積累跨項目共識。關掉：`NOKORI_PROMOTION_ENABLED=0`。

進度：`nokori status` 裏會看到 `shadow_hits` 和 `N/3 projects=...`。

### Async Extract Mode（關會話後自動挖規矩）

```bash
export NOKORI_EXTRACT_MODE=async
```

- **`manual`（默認）**：關會話只寫一個待辦文件，你自己跑 `nokori extract`  
- **`async`**：關會話時儘量在後臺跑 extract（已有進程在跑就只排隊，不重複開）  

日誌在 `~/.nokori/logs/async-extract.log`。沒配 LLM 時會嘗試本機 `claude -p` 兜底。

若 `{data_dir}/extract.lock` 已被佔用（另一實例正在跑 extract，或異常殘留），SessionEnd **不會**自動 spawn 子進程，pending job 保留，需稍後手動 `nokori extract`。

若 SessionEnd 之後 transcript 仍被追加（文件 `mtime` 變化），`nokori extract` 會**刷新 job 的 mtime 並保留 pending**，不會靜默丟棄 job。

損壞的 `extract-*.json`（無法解析）會在 `list_jobs` / `nokori extract` / `SessionStart` 維護時移到 `{data_dir}/jobs/bad/`，避免殭屍 job 佔目錄。

可選：`NOKORI_EXTRACT_DEFER_ACTIVE=1` 時，async 模式下若仍有**其他未 SessionEnd 的 session**（`active_sessions/` 裏 `ended_at` 爲空，`count_open_sessions`），當前 SessionEnd **只寫 job、不 fork** `nokori extract`；待其它 session 結束後再手動或下次 SessionEnd 觸發提取。

`NOKORI_SESSION_IDLE_SECONDS`（`[session] idle_seconds`）**不參與** defer，僅用於 `nokori status` 的「active」展示（open + 近期有 `touch` 心跳）。

Extract jobs 由 `nokori extract`（手動或 async 子進程）消費；**`async` 模式下 SessionStart** 若發現 pending job 且 extract 鎖空閒，會**後臺重試** spawn extract。`nokori extract` 使用 `{data_dir}/extract.lock`（Unix / Windows 均支持）防止併發重複處理；若已有實例在跑則 **exit 2** 並打印 `(extract already running)`（與「無 pending job」的 exit 0 區分）。

### 熱緩存

SessionStart 找「上一場 transcript」：

1. **優先**讀 `{data_dir}/transcript_index/`（SessionEnd 寫入的 previous/current 指針）——表示**上一個在該目錄正常結束的 session** 的文件，不一定是 mtime 最大的更早 `*.jsonl`。
2. **回退**：同目錄下 mtime 嚴格早於當前文件的最新 `*.jsonl`（啓發式，最多掃描 50 個文件）。

若上一場尚未 extract 過，從文件**尾部**讀取最後 3 條 user 消息注入（500 chars，獨立預算）。**Dormant 僞 HOT、shadow 計數、HOT 的 `hit_count`** 均在 **UserPromptSubmit 當輪** 寫庫，不等到下次 SessionStart。

**Shadow 與 candidate 激活**：跨項目 shadow HOT 會 `add_evidence(..., shadow_hot, 1)`。若其它項目的規則仍是 `candidate`，多次（不同天）shadow 命中可能湊夠純 AI 激活條件（score≥2 且 2 個活躍日）——**與「只服務 promotion」的直覺不同，v0.1 有意允許**跨項目檢索證據參與激活。

### 維護

維護任務在 `SessionStart` 時自動觸發（按間隔檢查）：

- **Dormant 掃描**（每 7 天）：30 天未命中的 active → dormant
- **Candidate 清理**（掃描間隔最多每 30 天跑一次）：刪除 **created_at ≥20 日曆天** 的普通 candidate、**≥40 天** 的 `anti_pattern` candidate（非「活 30 天」）
- **Unmerge 檢查**（最多每 90 天）：`status=merged` 的規則若 `superseded_by` 指向的規則已刪除或 dormant/archived，則恢復爲 `dormant`；**candidate 清理刪除錨點規則後**也會立即做一次 orphan unmerge
- **Session 文件清理**：刪除 `active_sessions/` 裏已結束超過 60 天的 registry 文件
- **Injection 清理**（掃描間隔最多每 7 天）：刪除 **30 天前** 的 `injections` 行（dismiss 僅查 24h，留緩衝）

也可手動觸發：

```bash
nokori maintain
```

---

## 檢索引擎

> **怎麼找到相關規矩？** 先用關鍵詞（BM25），規則多了再加語義向量，最後用 RRF 合併兩榜。檔位 HOT/WARM 決定寫進上下文多少字。

### BM25（默認，零依賴）

- 文檔字段：`trigger_text`、`trigger_variants`、`search_terms`、**`action`**
- Latin text: lowercase word tokens（≥ 2 chars）
- CJK text: 以 bigram 爲主；單字 CJK 保留 unigram（提高 recall）
- 混合文本自動切換

### Embedding（嵌入向量，可選）

規則 **≥ 20 條**（看本條 prompt 要搜的那一批）且配了遠程 API 或裝了 `pip install nokori[local-embed]` 時，會自動加語義檢索。  
`NOKORI_EMBED_ENABLED=1` 可強制嘗試（小庫也可能首輪仍只用 BM25，見下）。

**兩套閾值（易混淆）**：

| 場景 | 計數範圍 | 作用 |
|------|----------|------|
| **SessionStart** `embed` kickstart | 全庫 `active+dormant` 條數 | 是否後臺拉起 embed server（≥20 即可能 spawn，與你當前項目只有幾條規則無關） |
| **UserPromptSubmit** 檢索 | 當次 formal∪shadow 池大小 | 本條 prompt 是否走 embedding RRF |

**半索引**：啓用 embed 後，**沒有** `rule_embeddings` 行的規則在 RRF 裏只靠 BM25（剛 activate、import 後未索引、索引失敗時會出現）。語義檢索只使用與**當前配置的 embed 模型名**一致的 `rule_embeddings` 行；換模型或維度後請 `reindex` / 重新 `add` 或 `import` 觸發索引。`nokori health` 的 `embed.index` 會 warn 缺失條數；遠端點探測僅 **HTTP 2xx** 記爲 ok（401/404 不算健康）。

遠程 API 模式：

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
# NOKORI_EMBED_DIMENSIONS 默認不傳（用模型自身維度），僅 OpenAI text-embedding-3 等支持該參數時設置
```

本地模型模式（無需配置 URL）：

```bash
pip install nokori[local-embed]
# 或開發安裝：pip install -e ".[local-embed]"
```

安裝 `[local-embed]` 時會安裝 **sentence-transformers>=3.0**（Granite 的 `encode_query` / `encode_document` 需要；ST 2.x 不支援）。**模型權重**（`ibm-granite/granite-embedding-97m-multilingual-r2`，約 97M 參數、384 維）在以下時機下載到 `~/.nokori/models/`（不在 hook 裏下載，避免超時）。使用者話走 `encode_query`，規則索引走 `encode_document`（Granite R2 檢索 API）。從舊預設模型升級後請執行 `nokori embed prefetch`，並對規則重新索引（`add` / `import` / 編輯 trigger 相關欄位），使 `rule_embeddings` 的 `model_version` 與新模型一致：

| 時機 | 說明 |
|------|------|
| `pip install …[local-embed]` | 裝包結束後自動 prefetch（`pip install -e` 同樣） |
| `nokori install` | 已裝 `[local-embed]` 即 prefetch，**與 hooks 是否已註冊無關** |
| `nokori embed prefetch` | 手動下載或失敗重試 |

未配置遠程 embed endpoint 且可檢索規則 ≥ 20 時，由 **embed 共享進程**從上述目錄加載模型。

Hook 行爲（`NOKORI_EMBED_SERVER_AUTO_START=1`，默認開）：

- **SessionStart**：若本地權重已在緩存目錄 → 非阻塞 `spawn` embed server；**缺權重只打日誌**，不阻塞、不在 hook 裏 `import sentence_transformers`
- **UserPromptSubmit**：若 server 尚未 `ping` 通 → 後臺 spawn、**當輪純 BM25**；下一輪起通常有 RRF
- 不在 hook 內等待模型下載或長時間加載（避免超過 Claude hook 超時）

`nokori embed start` 可提前拉起；`NOKORI_EMBED_ENABLED=1` 會強制嘗試 embed（即使規則 <20），小庫首條仍可能 BM25-only。

優先級：遠程 API（配了 base_url）> 本地 embed server（裝了 `[local-embed]`）> 純 BM25。server 未就緒時回退 BM25，不在每個 hook 子進程裏再加載一遍模型。

兩種分數會經 **RRF**（排名融合）合成一張總榜，再分 HOT/WARM。

**平臺說明**：本地 embed 僅 **macOS / Linux**（`embed.sock`）。Windows 上爲純 BM25 或遠程 `NOKORI_EMBED_BASE_URL`。

本地 embed 管理（Unix）：

```bash
nokori embed prefetch # 下載本地模型權重（pip/install 已做過可跳過）
nokori embed start    # 後臺拉起共享 server（hook 也會按需自動 start）
nokori embed status   # 進程 / socket / idle 配置
nokori embed stop     # 優雅關閉（SIGTERM + IPC shutdown）
# nokori embed serve  # 前臺調試；空閒超過 NOKORI_EMBED_SERVER_IDLE 秒自動退出
```

本地 embed server 的 Unix socket 在 `NOKORI_DATA_DIR` 下，**無 IPC 鑑權**（本機單用戶場景可接受；勿把數據目錄放在多用戶共享路徑）。

### 注入分層

| 層級 | 條件 | 注入內容 |
|------|------|----------|
| HOT | top-1 且顯著高於 top-2 + 最低證據通過；**僅 1 條命中**時還需 `rrf_score > 0.01` 且 ≥3 個 matched token | trigger + action + rationale |
| WARM | top-5 內其餘（含最低證據） | trigger + action 一行 |
| COLD | top-5 外 | 不注入 |

**最低證據**：≥2 個 query token 重疊；或 1 token + trigger variant 命中；或 embedding cosine ≥ 0.55。純 embedding 命中時 `matched_tokens` 可能爲空（仍可通過 cosine 門檻進入 HOT/WARM）。

注入預算：1500 chars（規則）+ 500 chars（熱緩存，獨立）。僅**實際寫入上下文**的規則會記入 `injections` 並更新 `last_hit` / HOT 的 `hit_count`（預算截斷的不記）。

---

## CLI 完整參考

```bash
# 規則管理
nokori add [--trigger "..." --action "..." --source-type ... --confidence ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]

# 提取
nokori extract [--session <path>] [--dry-run]

# 調試
nokori test "<prompt>" [--project <id>]
nokori status          # 含 promotion 進度：每條 project 規則 N/3 個不同 project 已 shadow HOT
nokori logs
nokori health

# 維護
nokori maintain
nokori reset [--force]   # 非交互終端須加 --force

# 本地 embed 共享進程（Unix；可選）
nokori embed prefetch | start | stop | status

# 導入導出（JSON 的 version 字段 = rules.db schema，當前爲 2）
nokori export <path.json>
nokori import <path.json>

# 安裝
nokori install [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

---

## 環境變量

| 變量 | 默認值 | 說明 |
|------|--------|------|
| `NOKORI_DATA_DIR` | `~/.nokori` | 數據根目錄 |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | 注入字符上限 |
| `NOKORI_GATE_ENABLED` | `1` | 啓用 gate |
| `NOKORI_GATE_TTL_SECONDS` | `600` | Marker 過期時間；`0` = 永不過期 |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | **第二層**：hook 內 block 的 `tool_name` 正則（任意工具用 `.*`）；見 [Gate 兩層匹配](#gate-與-pretooluse兩層工具匹配) |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` 時 async 模式有活躍 session 則推遲 fork extract |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | `active_sessions` 無心跳超過此秒數視爲非活躍 |
| `NOKORI_HOT_CACHE` | `1` | SessionStart 熱緩存 |
| `NOKORI_PROMOTION_ENABLED` | `1` | 影子池與 cross-project promotion；`0` 關閉場景 C |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | hook 遠程 embed 超時（秒） |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | 本地 embed 進程空閒退出（秒） |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | hook 按需自動拉起 embed server |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions 端點 |
| `NOKORI_LLM_MODEL` | — | LLM 模型名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_EMBED_ENABLED` | `0`（active+dormant≥20 自動） | 強制啓用 embedding |
| `NOKORI_EMBED_BASE_URL` | — | OpenAI-compatible embeddings 端點 |
| `NOKORI_EMBED_MODEL` | — | Embedding 模型名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0`（不傳，用模型默認） | 向量維度（僅支持該參數的模型需要設） |
| `NOKORI_EMBED_CHUNK_SIZE` | `4000` | 文本分塊字符數 |
| `NOKORI_EMBED_CHUNK_COUNT` | `2` | 每規則最多分塊數 |
| `NOKORI_STRICT` | `0` | `1` 時 hook 異常向上拋出（調試；默認 fail-open） |
| `NOKORI_DISABLED` | `0` | 完全禁用 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | 對話裏退役規則的動詞（`動詞 + short_id`）；見 [Dismiss](#4-規則過時了dismiss) |
| `NOKORI_LOG_LEVEL` | `warn` | 日誌級別 |

**僅環境變量**（無 `config.toml` 字段，見 [config.toml.example](config.toml.example)）：

| 變量 | 默認值 | 說明 |
|------|--------|------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | `nokori install` 讀寫的 `settings.json` 目錄 |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | 額外允許讀取 transcript 的根目錄，`os.pathsep` 分隔（路徑安全校驗） |
| `NOKORI_EXTRACTING` | — | 內部：`claude -p` fallback 子進程防遞歸；勿在用戶 shell 或 async extract 中設置 |

所有 LLM/Embedding 端點兼容：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任何 `/v1/chat/completions` + `/v1/embeddings` 端點。

---

## 配置文件

除環境變量外，Nokori 支持 TOML 配置文件 `~/.nokori/config.toml`（路徑隨 `NOKORI_DATA_DIR`）。

倉庫根目錄提供完整模板 **[config.toml.example](config.toml.example)**（全部可配置項、默認值、可選值與說明）。

**優先級**：環境變量 > config.toml > 內置默認值。

```toml
# ~/.nokori/config.toml

log_level = "info"
dismiss_phrase = "dismiss"

[llm]
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-v4-flash"
api_key = "sk-xxx"

[embed]
# 遠程 OpenAI-compatible API（與下方 server 參數同屬一張 [embed] 表，勿重複寫兩個 [embed] 表頭）
base_url = "https://api.example.com/v1"
model = "text-embedding-v4"
api_key = "sk-xxx"
# dimensions = 0  # 不填或 0 = 不傳給 API（用模型默認維度）
chunk_size = 4000
chunk_count = 2
enabled = true
# 本地 embed 共享進程（未配 base_url 且 pip install nokori[local-embed] 時）
# hook_timeout_seconds = 2
# server_idle_seconds = 3600
# server_auto_start = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
# defer_when_active = false   # 有其他 open session 時推遲 async extract

[hot_cache]
enabled = true

[promotion]
enabled = true

[session]
# idle_seconds = 1800
```

所有字段與環境變量一一對應（見 [config.toml.example](config.toml.example) 速查表）。文件不存在時靜默忽略，純環境變量模式照常工作。

**注意**：`[gate] matcher` 隻影響 Nokori hook **內部**是否 block；PreToolUse **是否調用 hook** 由 `~/.claude/settings.json` 決定，見上文 [Gate 兩層匹配](#gate-與-pretooluse兩層工具匹配)。`dismiss_phrase` 的完整說明見 [Dismiss](#4-規則過時了dismiss)。

---

## 數據存儲

所有數據存儲在本地 `~/.nokori/`：

```
~/.nokori/
├── config.toml           # 配置文件（可選，env vars 優先）
├── rules.db              # SQLite (WAL mode): 規則 + 索引 + 元數據
├── jobs/                 # Extract job 隊列
├── active_sessions/      # Session registry
├── gate_markers/         # Gate markers（按 session + prompt_hash）
├── logs/
│   ├── hook.log          # Hook 進程日誌
│   ├── pipeline.log      # 提取/合併日誌
│   ├── async-extract.log # async 模式子進程 stderr
│   └── embed-server.log  # 本地 embed server（若啓用）
├── models/               # 本地 embed 權重（pip [local-embed] / install / embed prefetch）
├── embed.sock            # 本地 embed IPC（Unix）
└── extract.lock          # extract 單實例鎖
```

- 零網絡同步，純本地
- 規則不包含源代碼，只含行爲描述
- LLM 調用發送壓縮後的 transcript 片段（非源代碼）
- 可指向本地 Ollama 實現完全離線
- **數據庫**：與當前 nokori 版本綁定；換機或升級後若打不開庫，請 `nokori export` 備份，或換新 `NOKORI_DATA_DIR` / `nokori reset`。

---

## 與現有系統的關係

| 系統 | 關係 |
|------|------|
| CLAUDE.md | 互補。Nokori 不改你的 CLAUDE.md；規矩是動態的「遇到 X 就做 Y」 |
| Claude Code auto-memory | 不衝突。memory 偏事實，Nokori 偏行爲規矩 |
| 其他 memory 插件 | hook 可共存，但建議別疊太多「往上下文塞字」的插件 |

---

## 開發

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3.11+ -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/   # 勿用系統 python -m pytest（可能 0 collected）
```

項目約束：
- 零運行時依賴（`dependencies = []`）
- 純 Python stdlib + urllib 調用 API
- 交互熱路徑（UserPromptSubmit / PreToolUse）禁止 LLM 調用
- 所有 hooks 頂層 try/except，失敗返回 pass-through

---

## License

MIT
