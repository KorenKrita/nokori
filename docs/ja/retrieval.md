# 検索エンジン

[← メインドキュメントへ戻る](../../README.ja.md)

---

全ルールの中から現在のプロンプトに関連する数件をどう選ぶか。3 段階で行う：BM25 でキーワードスコアを算出し、ルールが十分あれば意味ベクトル（embedding）を重ね、RRF で二つのランキングを融合する。最後に HOT / WARM の段階でコンテキストに含める文字量を決める。

---

## BM25（デフォルト、依存ゼロ）

すぐ使える。モデルも GPU も不要。

- インデックスフィールド：`trigger_text`、`trigger_variants`、`search_terms`、`action`
- ラテン文字：小文字化して分割、長さ >= 2 のトークンのみ収める
- CJK：bigram（隣接する 2 文字）を主体に、孤立した 1 文字は unigram として保持し recall を向上
- 日本語・英語混在は自動処理

---

## Embedding（埋め込みベクトル、オプション）

ルールが **>= 20 件**に達し、リモート API を設定しているか `pip install nokori[local-embed]` を導入していれば、意味検索が自動で重畳される。強制的に試すなら `NOKORI_EMBED_ENABLED=1`。

「20」と呼ばれる閾値は 2 つあり、数えるルール集合が異なる：

| 場面 | 数える対象 | 決定する内容 |
|------|-----------|----------|
| **SessionStart** の embed kickstart | 全ライブラリの `active + trusted` 総数 | バックグラウンドで embed server を起動するか |
| **UserPromptSubmit** の検索 | 当回の `formal ∪ shadow` プールサイズ | この prompt で embedding RRF を使うか |

### リモート API モード

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
```

### ローカルモデルモード

```bash
pip install nokori[local-embed]
```

インストール時に **sentence-transformers>=3.0** が入る。prefetch されるモデルは [ibm-granite/granite-embedding-97m-multilingual-r2](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)（97M パラメータ / 384 次元、約 220MB）。

| 構成要素 | サイズ（約） |
|----------|------------|
| `model.safetensors` | ~186 MiB |
| `tokenizer.json` + config | ~24 MiB |
| **合計** | ~210-220MB |

ウェイトのダウンロードタイミング：

| タイミング | 説明 |
|------|------|
| `pip install …[local-embed]` | パッケージインストール後に自動 prefetch |
| `nokori install` | `[local-embed]` 済みなら prefetch |
| `nokori embed prefetch` | 手動ダウンロードまたはリトライ |

### Hook 内での embed server の挙動

- **SessionStart**：ローカルウェイトがキャッシュ済みならノンブロッキングで embed server を spawn
- **UserPromptSubmit**：server がまだ ping で通らなければバックグラウンドで spawn し、当ターンは純 BM25
- Hook はモデルのダウンロードやロード完了を待たない

優先順位：リモート API > ローカル embed server > 純 BM25。

### ローカル embed 管理（Unix）

```bash
nokori embed prefetch   # ウェイトをダウンロード
nokori embed start      # バックグラウンドで server を起動
nokori embed status     # 状態を確認
nokori embed stop       # グレースフルに終了
```

**プラットフォーム**：ローカル embed は macOS / Linux でのみ動作（Unix socket）。Windows はリモート API か純 BM25 を使う。

---

## 注入の階層化

検索後、スコアで 3 段階に分ける：

| 階層 | 入る条件 | 注入内容 |
|------|---------|----------|
| HOT | runtime applicability を通った `active`/`trusted` で utility が正。通常最大 1 件 | trigger + action + rationale |
| WARM | エビデンスは通るが utility/history/budget が HOT に届かない | trigger + action、一行 |
| COLD | Candidate/suppressed/archived、excluded、trigger エビデンス不足 | 注入しない |

**Trigger evidence** はルールの trigger 構造に由来する必要がある：strong variant phrase + required concepts、または十分な dynamic IDF trigger information。Action-only、search-term-only、embedding-only、excluded-context、near-miss は COLD のまま。

注入予算：ルール 1500 文字、ホットキャッシュ 500 文字（相互独立）。実際にコンテキストへ書き込まれたルールだけが fire event として記録される。
