# Gate 机制

[← 返回主文档](../../README.zh-CN.md)

---

> **Gate 是什么？** 不是全程禁用工具，而是「本轮第一次调用敏感工具前，先让 Agent 看到相关规则」。拦截一次后清除标记，同一条消息内后续工具照常执行。

---

## 两层「工具匹配」

Gate 永远有两层判断：

1. **当前 runtime 要不要在工具运行前先调用 Nokori？**
2. **如果调用了，当前 `tool_name` 要不要被拦一次？**

Runtime 层：

- **Claude Code**：`~/.claude/settings.json` 的 `PreToolUse.matcher`
- **Cursor**：`~/.cursor/hooks.json` 的原生 pre-tool matcher
- **OMP**：安装到 `~/.omp/agent/extensions/nokori.ts` 的 bridge，在 `tool_call` 上触发

Nokori 层：

- **配置**：`~/.nokori/config.toml` 的 `[gate] matcher`，或环境变量 `NOKORI_GATE_MATCHER`
- **匹配方式**：Python `re.fullmatch` 匹配 `payload.tool_name`

Gate 阻断时，Claude Code / Cursor 返回 `hookSpecificOutput.permissionDecision: "deny"` 与 `permissionDecisionReason`；OMP 则通过 bridge 返回同样原因的 tool-call block。

---

## 第一层：让 hook / bridge 在哪些工具上运行

- **运行时文件**：Claude Code 用 `~/.claude/settings.json`，Cursor 用 `~/.cursor/hooks.json`，OMP 用 `~/.omp/agent/extensions/nokori.ts`
- **Claude Code / Cursor 默认值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **OMP 说明**：bridge 会接收每个 `tool_call`；OMP 的工具名是小写，如 `bash`、`edit`、`write`、`grep`、`glob`、`read`
- **想让任意工具都先进入这一层**：把运行时 matcher 改成对应平台支持的全匹配

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

## 第二层：对哪些 `tool_name` 真正 block

- **配置文件**：`~/.nokori/config.toml` 的 `[gate] matcher`
- **匹配方式**：Python `re.fullmatch` 匹配 payload 里的 `tool_name`
- **Claude Code / Cursor 默认值**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **OMP 默认值**：`bash|edit|write`；`read`、`grep`、`glob` 等只读工具默认不会被 Gate，除非手动扩大 matcher
- **想让任意工具都可被 Gate**：设为 `.*`（不是 `*`）

```toml
[gate]
matcher = ".*"
```

两层都要一起改，才能达到「任意工具都可能被 Gate」。

---

## 其它 Gate 配置

| 项 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 总开关 |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | marker 有效期（默认 600s）；`0` = 永不过期 |

---

## Prompt-hash 安全

`UserPromptSubmit` 写入 marker 时记录 prompt hash。`PreToolUse` 校验 hash 一致性——若不一致（用户已发下一条消息），删除 marker 并放行，不 block。
