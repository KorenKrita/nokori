# CLI 参考

[← 返回主文档](../../README.zh-CN.md)

---

## 规则管理

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
nokori extract                    # 消费所有待处理 job
```

---

## 调试

```bash
nokori test "<prompt>" [--project <id>]
nokori status                     # 规则状态、hook/config、embed 与生命周期证据
nokori logs
nokori health
```

---

## 可观测性（AI 友好）

```bash
nokori report [--since <ISO>] [--session <id>] [--json]
nokori stream [--since <ISO>] [--session <id>] [--type <source>] [--verbose] [--follow]
```

---

## 维护

```bash
nokori maintain
```

---

## 本地 Embed

```bash
nokori embed prefetch | start | stop | status
```

---

## 导入导出

```bash
nokori export <path.json>
nokori import <path.json>
```

JSON 的 `version` 字段 = rules.db schema，当前为 2。

---

## 安装管理

```bash
nokori install [--claude | --cursor | --all]
               [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

---

## Web UI

```bash
nokori web                        # 自动打开 http://localhost:8765
nokori web --port 9000            # 自定义端口
nokori web --no-browser           # 仅启动服务器
```
