# Gate 机制

[← 返回主文档](../../README.zh-CN.md)

---

> **Gate 是什么？** 不是全程禁用工具，而是「本轮第一次调用敏感工具前，先让 Claude 看到相关规则」。拦截一次后清除标记，同一条消息内后续工具照常执行。

---

## 两层「工具匹配」

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

Gate 阻断时 hook 返回 `hookSpecificOutput.permissionDecision: "deny"` 与 `permissionDecisionReason`。

---

## 第一层：让 hook 在哪些工具上运行

- **配置文件**：`~/.claude/settings.json`（`nokori install` 写入）
- **默认值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成任意工具**：把 matcher 改为 `*`

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

## 第二层：hook 内对哪些 tool_name 真正 block

- **配置文件**：`~/.nokori/config.toml` 的 `[gate] matcher`
- **Python `re.fullmatch`** 匹配 payload 里的 `tool_name`
- **默认值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **改成任意工具**：设为 `.*`（不是 `*`）

```toml
[gate]
matcher = ".*"
```

两层要一起改才能达到「任意工具都可能被 Gate」。

---

## 其它 Gate 配置

| 项 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 总开关 |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | marker 有效期（默认 600s）；`0` = 永不过期 |

---

## Prompt-hash 安全

`UserPromptSubmit` 写入 marker 时记录 prompt hash。`PreToolUse` 校验 hash 一致性——若不一致（用户已发下一条消息），删除 marker 并放行，不 block。
