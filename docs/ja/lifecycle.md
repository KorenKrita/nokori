# ルールのライフサイクル

[← メインドキュメントへ戻る](../../README.ja.md)

---

## 状態機械

```
candidate → active → trusted
      │          │         │
      └──────────┴─────────┴→ suppressed → candidate（回復自動化のみ）
                              └→ archived（終端）
```

| 状態 | リマインドに出る？ | Gate する？ | どう来たか |
|------|-----------|-----------|----------|
| `candidate` | いいえ。shadow / エビデンスのみ | いいえ | `nokori add` またはコールドパス抽出 |
| `active` | はい。有用性観測前は最大 WARM | Gate しない | コールドパス fast lane または shadow エビデンスによる昇格 |
| `trusted` | はい | 可能（`gate_eligible` のみ） | 有用性が観測された後に自律ライフサイクルが信頼を付与 |
| `suppressed` | いいえ。shadow recovery のみ | いいえ | false-positive / harmful エビデンス |
| `archived` | いいえ | いいえ | ユーザーの dismiss またはアーカイブポリシー |

---

## active / trusted になる条件

- **手動 `nokori add` は常に `candidate` を作る**。`--severity high_risk` でもライフサイクルを飛ばせない。
- **コールドパス fast-lane で active に到達**するには、matcher コンパイル、archived 指紋チェック、merge policy、synthetic eval、cold-fast-lane 閾値が必要。
- **Candidate → active の昇格**はシャドウエビデンスによる。複数セッションにわたり十分なシャドウマッチが蓄積されれば synthetic eval は不要。
- **trusted / gate-capable** には自律 posthoc / shadow エビデンスが必要。`nokori edit --status` は意図的に拒否される。

---

## ランタイムエビデンスと posthoc

ホットパスは trigger データをコンパイルし、required concepts / exclusions を確認し、dynamic IDF trigger evidence を適用し、fire events を記録し、SessionEnd 後に posthoc 評価をキューに入れる。

---

## Project ID

Nokori は `git rev-parse --show-toplevel` でプロジェクトルートを見つけ、`<ディレクトリ名>-<パスハッシュ先頭8桁>` を project_id にする。git ディレクトリでなければ cwd にフォールバック。

### Project / global scope

- `project_scope=project`：本プロジェクト + global ルール
- `project_scope=global`：正式プール入りが許されれば全プロジェクトで有効

スコープは trust を迂回する手段ではない。

---

## メンテナンスタスク

メンテナンスは `SessionStart` に紐づき、各タスクが間隔に達した時だけ実行される：

| タスク | 間隔 | 説明 |
|------|------|------|
| ライフサイクル遷移 | 毎日 | posthoc/shadow エビデンスで状態を更新 |
| Candidate 清掃 | 最大 30 日ごと | 20 日経過の通常 candidate、40 日経過の anti_pattern を削除 |
| Replacement 回復チェック | 最大 90 日ごと | archived replacement の target が存在しなければ復元 |
| Session ファイル清掃 | — | 終了から 60 日超の registry を削除 |
| Hook coalesce 清掃 | — | 24 時間超の claim ファイルを削除 |
| Prompt ack 清掃 | — | 24 時間超の ack/deferred を削除 |
| Fire event 清掃 | 最大 7 日ごと | 30 日前の fire events を削除 |

すぐ実行したい場合：

```bash
nokori maintain
```

---

## データベース

すべてのルールは SQLite ファイル `rules.db` に格納され、初回利用時に自動作成される。マシン移行やアップグレードで開けなくなったら、まず `nokori export` でバックアップを取ること。
