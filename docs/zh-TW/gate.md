# Gate 機制

[← 返回主文件](../../README.zh-TW.md)

---

> **Gate 是什麼？** 不是全程禁用工具，而是「本輪第一次呼叫敏感工具前，先讓 Claude 看到相關規則」。攔截一次後清除標記，同一條消息內後續工具照常執行。

---

## 兩層「工具匹配」

```
Claude 準備呼叫工具
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第一層：Claude Code settings.json 的 PreToolUse.matcher │
│ 「要不要執行 nokori hook pre-tool-use」                    │
│ 預設：Edit|Write|MultiEdit|Bash|NotebookEdit            │
│ Read / Grep 等預設不會進 hook                            │
└─────────────────────────────────────────────────────────┘
    │ hook 已執行
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第二層：Nokori [gate].matcher（NOKORI_GATE_MATCHER）       │
│ 「hook 裡要不要對這次 tool_name 做 block」               │
│ 預設：同上；須為 Python 正則，對 payload.tool_name fullmatch│
└─────────────────────────────────────────────────────────┘
    │ 有 marker 且匹配
    ▼
  deny 一次 → 刪 marker → 重試同工具則放行
```

Gate 阻斷時 hook 回傳 `hookSpecificOutput.permissionDecision: "deny"` 與 `permissionDecisionReason`。

---

## 第一層：讓 hook 在哪些工具上執行

- **設定檔**：`~/.claude/settings.json`（`nokori install` 寫入）
- **預設值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成任意工具**：把 matcher 改為 `*`

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

---

## 第二層：hook 內對哪些 tool_name 真正 block

- **設定檔**：`~/.nokori/config.toml` 的 `[gate] matcher`
- **Python `re.fullmatch`** 匹配 payload 裡的 `tool_name`
- **預設值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成任意工具**：設為 `.*`（不是 `*`）

```toml
[gate]
matcher = ".*"
```

兩層要一起改才能達到「任意工具都可能被 Gate」。

---

## 其它 Gate 設定

| 項 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 總開關 |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | marker 有效期（預設 600s）；`0` = 永不過期 |

---

## Prompt-hash 安全

`UserPromptSubmit` 寫入 marker 時記錄 prompt hash。`PreToolUse` 校驗 hash 一致性——若不一致（使用者已發下一條消息），刪除 marker 並放行，不 block。
