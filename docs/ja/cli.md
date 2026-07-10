# CLI リファレンス

[← メインドキュメントへ戻る](../../README.ja.md)

---

## ルール管理

```bash
nokori add --trigger "..." --action "..." [--severity reminder|high_risk] [--variants ...] [--terms-en ...] [--terms-zh ...] [--project-id ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]
```

---

## 抽出

```bash
nokori extract [--session <path>] [--dry-run]
nokori extract                    # 保留中の job をすべて消化
```

---

## デバッグ

```bash
nokori test "<prompt>" [--project <id>]
nokori status                     # ルール状態、hook/config、embed、ライフサイクルエビデンス
nokori logs
nokori health
```

---

## オブザーバビリティ（AI フレンドリー）

```bash
nokori report [--since <ISO>] [--session <id>] [--json]
nokori stream [--since <ISO>] [--session <id>] [--type <source>] [--verbose] [--follow]
```

---

## メンテナンス

```bash
nokori maintain
```

---

## ローカル Embed

```bash
nokori embed prefetch | start | stop | status
```

---

## インポート / エクスポート

```bash
nokori export <path.json>
nokori import <path.json>
```

JSON の `version` フィールド = rules.db スキーマ。現在は 2。

---

## インストール管理

```bash
nokori install [--claude] [--cursor] [--pi] [--omp] [--all]
               [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

`--all` は Claude Code + Cursor を意味する。必要なら明示的な `--pi` / `--omp` と組み合わせられる。

---

## Web UI

```bash
nokori web                        # http://localhost:8765 を自動で開く
nokori web --port 9000            # カスタムポート
nokori web --no-browser           # サーバーのみ起動
```
