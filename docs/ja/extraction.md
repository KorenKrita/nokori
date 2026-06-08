# 自動抽出

[← メインドキュメントへ戻る](../../README.ja.md)

---

セッション終了後に実行されるコールドパス。対話のホットパスには乗らない。LLM を設定しておけば、Nokori はそのセッションの transcript を読み、候補ルールを抽出し、コールドパスパイプラインに通す。

```bash
# LLM を設定（任意の OpenAI-compatible エンドポイント）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手動抽出
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# dry-run プレビュー
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 保留中の job をすべて消化
nokori extract
```

---

## 1 本の transcript がルールになるまで

コールドパスはホットパスより意図的に慎重だ。曖昧なルールを正式プールに入れないため、多段階で判定する：

1. **読み込み**：transcript を読む。単一ファイル上限 50MB
2. **圧縮**：ユーザーメッセージは原文保持、AI 応答は先頭 200 字 + 末尾 100 字に切り詰め。全体を約 30k token 以内に圧縮
3. **抽出**：extractor ロールが構造化 candidate を出力
4. **判定 / 書き換え / 再判定**：admission judge と final judge が弱いエビデンス・広すぎるスコープを拒否
5. **マージ計画**：merge planner が近傍ルールとの関係を比較
6. **検証・保存**：archived 指紋、matcher コンパイル、cold-fast-lane 閾値が candidate か active として保存するかを決定

**LLM 呼び出し形式**：各ロールは system + user の 2 メッセージに分ける。transcript 断片は `--- BEGIN UNTRUSTED DATA ---` / `--- END UNTRUSTED DATA ---` の区切りブロックで囲む。

---

## マージ戦略

LLM が各候補に関係文字 `A`-`E` を返す：

| 判定 | 動作 |
|------|------|
| **SAME (A)** | merge_into_existing / replace / reject |
| **BROADER (B)** | 安全性/品質の判断後に決定 |
| **NARROWER (C)** | 新ルールを挿入、既存と共存 |
| **CONTRADICTS (D)** | 保守的に keep_both または reject_new |
| **UNRELATED (E)** | 新しい candidate を 1 件挿入 |

失敗時の処理：

- **抽出 LLM の失敗**：job は pending のまま
- **Merge LLM の失敗**：当該候補をスキップ、job は pending のまま

**近傍バックフィル**：BM25 の事前スクリーニングで 5 件に満たないとき、`updated_at` が新しいルールを上限まで補填する。

---

## Async Extract モード

```bash
export NOKORI_EXTRACT_MODE=async
```

| モード | 動作 |
|------|------|
| `manual`（デフォルト） | セッションを閉じるとジョブファイルだけ書く。抽出は手動 `nokori extract` |
| `async` | セッション終了時にバックグラウンドで直接 extract を実行 |

ログ：`~/.nokori/logs/async-extract.log`。LLM 未設定時はローカルの `claude -p` にフォールバック。

エッジケース：

- `extract.lock` が取得済み：自動起動しない。pending job は保持
- Transcript の mtime が変化：job の mtime を更新、pending を維持
- 破損した job ファイル：`jobs/bad/` へ移動
- `NOKORI_EXTRACT_DEFER_ACTIVE=1`：未終了のセッションがある間は fork しない
