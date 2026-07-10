# CLI 參考

[← 返回主文件](../../README.zh-TW.md)

---

## 規則管理

```bash
nokori add --trigger "..." --action "..." [--severity reminder|high_risk] [--variants ...] [--terms-en ...] [--terms-zh ...] [--project-id ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]
```

---

## 提取

```bash
nokori extract [--session <path>] [--dry-run]
nokori extract                    # 消費所有待處理 job
```

---

## 偵錯

```bash
nokori test "<prompt>" [--project <id>]
nokori status                     # 規則狀態、hook/config、embed 與生命週期證據
nokori logs
nokori health
```

---

## 可觀測性（AI 友善）

```bash
nokori report [--since <ISO>] [--session <id>] [--json]
nokori stream [--since <ISO>] [--session <id>] [--type <source>] [--verbose] [--follow]
```

---

## 維護

```bash
nokori maintain
```

---

## 本地 Embed

```bash
nokori embed prefetch | start | stop | status
```

---

## 匯入匯出

```bash
nokori export <path.json>
nokori import <path.json>
```

JSON 的 `version` 欄位 = rules.db schema，當前為 2。

---

## 安裝管理

```bash
nokori install [--claude] [--cursor] [--pi] [--omp] [--all]
               [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

`--all` 表示 Claude Code + Cursor；需要時可再組合明確的 `--pi` / `--omp`。

---

## Web UI

```bash
nokori web                        # 自動開啟 http://localhost:8765
nokori web --port 9000            # 自訂連接埠
nokori web --no-browser           # 僅啟動伺服器
```
