# 規則生命週期

[← 返回主文件](../../README.zh-TW.md)

---

## 狀態機

```
candidate → active → trusted
      │          │         │
      └──────────┴─────────┴→ suppressed → candidate（僅恢復自動化可做）
                              └→ archived（終態）
```

| 狀態 | 參與提醒？ | 會 Gate？ | 怎麼來的 |
|------|-----------|-----------|----------|
| `candidate` | 否；只做 shadow / 證據 | 否 | `nokori add` 或冷路徑提取 |
| `active` | 是；未觀察到有用前最多 WARM | 不會直接 Gate | 冷路徑 fast lane 或 shadow 證據推動 |
| `trusted` | 是 | 可能（僅 `gate_eligible`） | 觀察到實際有用後由自治生命週期授信 |
| `suppressed` | 否；只做 shadow recovery | 否 | false-positive / harmful 證據 |
| `archived` | 否 | 否 | 使用者 dismiss 或歸檔策略 |

---

## 怎麼變 active / trusted

- **手動 `nokori add` 永遠建立 `candidate`**。即使 `--confidence high --source-type correction`，也不會繞過生命週期。
- **冷路徑 fast-lane 直達 active** 要通過 matcher 編譯、歸檔指紋檢查、merge policy、synthetic eval 與 cold-fast-lane 閾值。
- **Candidate → active 晉升**透過影子證據；跨多個 session 累積足夠影子匹配則不需要 synthetic eval。
- **trusted / gate-capable** 需要自治 posthoc / shadow 證據；`nokori edit --status` 會被刻意拒絕。

---

## 執行時證據與 posthoc

熱路徑會編譯 trigger 資料，檢查 required concepts / exclusions，應用動態 IDF trigger evidence，記錄完整 fire events，並在 SessionEnd 後排 posthoc 評估。

---

## Project ID

Nokori 用 `git rev-parse --show-toplevel` 找專案根，拼出 `<目錄名>-<路徑 hash 前 8 位>` 當 project_id。不是 git 目錄就退回用 cwd。

### Project / global scope

- `project_scope=project`：本專案 + global 規則
- `project_scope=global`：進入正式池後可在所有專案可見

作用域不是繞過 trust 的捷徑。

---

## 維護任務

維護掛在 `SessionStart` 上，按各自間隔到點才跑：

| 任務 | 間隔 | 說明 |
|------|------|------|
| 生命週期遷移 | 每天 | posthoc/shadow 證據更新狀態 |
| Candidate 清理 | 最多每 30 天 | 刪 20 天的普通 candidate、40 天的 anti_pattern |
| Replacement 恢復檢查 | 最多每 90 天 | archived replacement 目標不在就恢復 |
| Session 檔案清理 | — | 刪結束超 60 天的 registry |
| Hook 合併清理 | — | 刪超 24 小時的 claim 檔案 |
| Prompt ack 清理 | — | 刪超 24 小時的 ack/deferred |
| Fire event 清理 | 最多每 7 天 | 刪 30 天前的 fire events |

手動立即執行：

```bash
nokori maintain
```

---

## 資料庫

所有規則存放在 SQLite 檔案 `rules.db` 中，首次使用時自動建立。換機或升級後打不開，先 `nokori export` 備份。
