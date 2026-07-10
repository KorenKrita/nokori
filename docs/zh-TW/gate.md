# Gate 機制

[← 返回主文件](../../README.zh-TW.md)

---

> **Gate 是什麼？** 不是全程禁用工具，而是「本輪第一次呼叫敏感工具前，先讓 Agent 看到相關規則」。攔截一次後清除標記，同一條消息內後續工具照常執行。

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

Gate 阻斷時，Claude Code 與 Cursor 的 hook 會回傳 `hookSpecificOutput.permissionDecision: "deny"` 與 `permissionDecisionReason`；Pi / OMP 則由各自安裝的橋接在 `tool_call` 回傳同樣的阻斷理由。

---

## 第一層：讓 hook 在哪些工具上執行

- **執行時檔案**：Claude Code 用 `~/.claude/settings.json`，Cursor 原生用 `~/.cursor/hooks.json`，Pi 用 `~/.pi/agent/extensions/nokori.ts`，OMP 用 `~/.omp/agent/extensions/nokori.ts`
- **Claude Code / Cursor 預設值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **Pi / OMP 補充**：橋接會接收每個 `tool_call`，工具名會是 `bash`、`edit`、`write`、`grep`、`read` 這類全小寫，OMP 另有 `glob`
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
- **Python `re.fullmatch`**：匹配 payload 裡的 `tool_name`
- **Claude Code / Cursor 預設值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **Pi / OMP 預設值**：`bash|edit|write`；`read`、`grep`、`glob` 等唯讀工具預設不會被 Gate，除非手動擴大 matcher
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

`UserPromptSubmit`（Pi / OMP 上對應 `before_agent_start`）寫入 marker 時記錄 prompt hash；`PreToolUse`（對應 `tool_call`）校驗 hash 一致性。若使用者已發下一條消息導致 hash 不一致，就刪除 marker 並放行。
