# Gate 機構

[← メインドキュメントへ戻る](../../README.ja.md)

---

> **Gate とは？** ツールを永続的に封じるのではなく、「このターンで敏感なツールを初めて呼ぶ前に、Claude に関連ルールを見せる」こと。一度差し止めたらマーカーを破棄し、同じメッセージ内の以降のツールは通常通り実行される。

---

## 二層の「ツールマッチ」

```
Claude がツールを呼ぼうとする
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第1層：Claude Code settings.json の PreToolUse.matcher  │
│ 「nokori hook pre-tool-use を実行するかどうか」            │
│ デフォルト：Edit|Write|MultiEdit|Bash|NotebookEdit       │
│ Read / Grep 等はデフォルトで hook に入らない              │
└─────────────────────────────────────────────────────────┘
    │ hook 実行済み
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第2層：Nokori [gate].matcher（NOKORI_GATE_MATCHER）       │
│ 「hook 内で今回の tool_name を block するか」             │
│ デフォルト：同上。Python 正規表現で tool_name を fullmatch  │
└─────────────────────────────────────────────────────────┘
    │ marker あり + マッチ
    ▼
  一度 deny → marker 削除 → 同じツールを再試行すれば許可
```

Gate 阻断時、hook は `hookSpecificOutput.permissionDecision: "deny"` と `permissionDecisionReason` を返す。

---

## 第1層：どのツールで hook を実行するか

- **設定ファイル**：`~/.claude/settings.json`（`nokori install` が書き込む）
- **デフォルト値**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **任意のツールで hook を実行する**：matcher を `*` に変更

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

## 第2層：hook 内でどの tool_name を実際に block するか

- **設定ファイル**：`~/.nokori/config.toml` の `[gate] matcher`
- **Python `re.fullmatch`** で payload の `tool_name` をマッチ
- **デフォルト値**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **任意のツールを block 対象にする**：`.*` を設定（リテラルの `*` ではない）

```toml
[gate]
matcher = ".*"
```

「どのツールも Gate の対象になりうる」状態にするには、両方の層を一緒に変える必要がある。

---

## その他の Gate 設定

| 項目 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 総スイッチ。オフなら注入のみで block しない |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | marker の有効期限（デフォルト 600s）。`0` = 無期限 |

---

## Prompt-hash 安全機構

`UserPromptSubmit` はマーカー書き込み時に prompt hash を記録する。`PreToolUse` はこの hash を検証し、一致しなければ（ユーザーが次のメッセージを送信済み）マーカーを削除してツールを通す。block はしない。
