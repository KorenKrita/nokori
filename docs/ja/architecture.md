# アーキテクチャ

[← メインドキュメントへ戻る](../../README.ja.md)

---

## 自律品質フライホイール

Nokori の核心は autonomous quality flywheel（自律品質フライホイール）。すべてのルールは、記憶（memory）から行動（behavior）へ進む前に、自ら信頼を勝ち取らなければならない。

このサイクルは意図的に三段階に分かれている：

- **Cold path（コールドパス）**：セッション終了後、多ロール LLM パイプラインが候補ルールの抽出・判定・書き換え・マージ・評価を行う。弱いルールは門前払い、広すぎるルールは狭め、危険なマージは拒否または分割する。
- **Hot path（ホットパス）**：チャット中、hook は決定的な検索・マッチ・スコアリング・マーカー読み書き・fail-open（障害時は通過）だけを行う。prompt と Agent の返信の間に LLM 待ちは一切ない。
- **Evidence loop（エビデンス回流）**：HOT/WARM 注入は fire events を生成。candidate/suppressed のシャドウヒットは反事実エビデンスを生成。maintenance（メンテナンス）が評価済みエビデンスからライフサイクル遷移を適用する。

このサイクルを実用的にしている要素：

- **Structured triggers（構造化トリガー）**：concepts、required concept groups、trigger variants、excluded contexts、tool tags、severity、source origin、runtime policy version、lineage metadata。緩い文章の塊ではない。
- **Autonomous lifecycle（自律ライフサイクル）**：`candidate → active → trusted`。`suppressed` からの回復や終端 `archived` もある。手動コマンドで archive はできるが、trust は偽造できない。
- **Conservative Gate（保守的ゲート）**：Gate は `trusted + gate_eligible` のルール向けの一回限りのブレーキであり、権限システムではない。
- **Hybrid retrieval（ハイブリッド検索）**：BM25 は常時利用可能。オプションで remote embedding またはローカル Granite 多言語モデルを足して意味検索を補い、RRF と runtime applicability（適用性判定）が HOT/WARM を決める。
- **ローカル優先**：SQLite、hook ログ、job キュー、Gate マーカー、embedding ウェイト、Web UI ステートはすべて `~/.nokori/` 配下。リモート LLM / embedding エンドポイントは必要時のみ有効化。
- **クロスツール観測性**：Claude Code / Cursor はネイティブ hook、Pi / OMP は `~/.pi/agent/extensions/nokori.ts` / `~/.omp/agent/extensions/nokori.ts` の小さな TypeScript ブリッジを使う。どのランタイムも同じ Python ディスパッチャへ接続し、`nokori test`、`status`、`health`、`logs`、`extract`、`maintain`、Web UI で発火理由を確認できる。

Nokori がもっとも大切にしている約束は抑制（restraint）。早めにリマインドはできるが、強い権限を持つにはエビデンスが必要で、助け始めたあともエビデンス審査を受け続ける。

---

## ランタイム対応

Nokori のホットパスは 1 本で、各ランタイムをそこへ写像する。Claude Code / Cursor は既存の Python hook を直接呼び、Pi / OMP は生成済み TypeScript ブリッジから同じディスパッチャへイベントを渡す。検索、Gate マーカー、job、ルール保存先はそのまま `~/.nokori/` 配下で、抽出時だけ `~/.pi/agent/sessions/**/*.jsonl` または `~/.omp/agent/sessions/**/*.jsonl` の現在セッション JSONL をローカルから読む。

| Claude Code / Cursor | Pi / OMP | やること | レイテンシ予算 |
|----------------------|----------|---------|---------------|
| `SessionStart` | `session_start` | セッション開始：前回未抽出の user 断片を任意で注入し、DB メンテナンスを起動 | <= 1.5s |
| `UserPromptSubmit` | `before_agent_start` | Agent がターンを始める前：ルール検索 → コンテキスト注入 → 必要なら Gate マーカーを書く | <= 500ms |
| `PreToolUse` | `tool_call` | ツール呼び出し前：マーカーがあれば**一度差し止め**、そのあとマーカーを破棄 | <= 50ms |
| `SessionEnd` | `session_shutdown` | セッション終了：ランタイムの session manager が示す現在セッションファイルから抽出 job を作り、async モードならそのローカル JSONL に対して抽出を走らせる | <= 200ms |

突き詰めると 2 つ：

1. **リマインド（注入）**——命中した掟を各ランタイムの注入チャネルに返し、Agent が返信する前に見えるようにする
2. **一度差し止め（Gate）**：`trusted` で `severity=gate_eligible`、prompt エビデンスが強く、tool-input エビデンスも通ったルールだけがツールを差し止める。通常の active ルールはリマインドのみ

Pi / OMP では、`session_start` は `pi.sendMessage(...)` を使う。これは lifecycle handler に戻り値ベースの注入チャネルがないため。`before_agent_start` は `message` を返す。こちらは `BeforeAgentStartEventResult.message` が型付きの注入チャネルだからである。Pi は reason が `reload` の `session_start` / `session_shutdown` を無視するため、`/reload` が起動時注入を重複させたり、現在の Nokori セッションを終了扱いしたりしない。Bridge の timeout 値は runtime budget であり、`session_shutdown` は意図的に短い 2s の teardown budget を保つ。

---

## 注入 vs 阻断

| | 注入（`additionalContext` / Pi・OMP ブリッジ経由の注入メッセージ） | Gate（PreToolUse deny / Pi・OMP の tool block） |
|--|------------------------------|-------------------------|
| ルール範囲 | 正式プールの HOT + WARM | 正式プールの HOT のサブセット |
| 状態 | `active` および `trusted` | `trusted` のみ |
| 重大度 | `reminder`、`high_risk`、`gate_eligible` | `gate_eligible` のみ |
| その他の条件 | required concepts、excluded contexts、動的 trigger エビデンス、選択予算を通過 | 加えて強い prompt エビデンス、現在の runtime policy、prompt hash が一致すること。tool-input が検査可能な場合は tool-input エビデンスも |

Gate は権限システムではなく、一度だけ踏むブレーキ。関連ルールを表示し、一度拒否し、マーカーを破棄する。同一メッセージ内の以降のツール呼び出しはそのまま通る。

---

## Shadow Pool（シャドウプール）

`UserPromptSubmit` のたびに、Nokori は**正式プール**と**シャドウプール**を分けて検索する。シャドウエビデンスがリマインドの HOT/WARM 枠を奪わないようにするためだ。

- **正式プール**：`active` + `trusted`。注入できるのはこのプールだけ
- **シャドウプール**：`candidate` + `suppressed`。注入も Gate もしない
- Candidate のシャドウマッチは candidate → active の反事実エビデンスになる
- Suppressed のシャドウマッチは suppressed → active の回復エビデンスになる

---

## ホットキャッシュ

SessionStart が「前回の transcript」を探す手順：

1. **優先**：`{data_dir}/transcript_index/` に SessionEnd が書いた previous/current ポインタを読む
2. **フォールバック**：同ディレクトリで mtime が現在ファイルより厳密に古い最新の `*.jsonl`

前回がまだ extract されていなければ、ファイル**末尾**から最後の user メッセージ 3 件を注入する（500 文字。ルール用 1500 文字の予算とは独立）。

---

## 用語早見表

| 用語 | 説明 |
|----|------|
| **hook** | Claude Code / Cursor のネイティブ hook、または Pi / OMP ブリッジがライフサイクルイベントで実行する handler |
| **injection**（注入） | マッチした掟を Agent がそのターンで見えるコンテキストに書き込むこと |
| **Gate**（ゲート） | `trusted` + `gate_eligible` ルール向け：最初にマッチしたツール呼び出しを一度 deny する |
| **marker**（マーカー） | そのターンの「先に Gate ルールを読んで」という一時メモ。一度使えば破棄 |
| **transcript** | 対話全体の `.jsonl` ログ |
| **trigger / action** | 掟の二つの半分：「どんな状況で」+「どうすべきか」 |
| **short_id** | 掟の短い ID（例：`a3f2b1`） |
| **dismiss** | 掟を退役させる |
| **HOT / WARM** | マッチ度の段階：かなり関連 / やや関連 |
| **BM25** | キーワードの重なりでスコア化。GPU 不要、デフォルトで使える |
| **embedding** | 意味的類似度でスコア化。オプションで有効化 |
| **RRF** | BM25 とベクトルのランキングを一枚の総合ランキングに統合するアルゴリズム |
| **fail-open** | Nokori 自体にエラーが起きても Claude を止めない |
| **extract** | transcript から LLM で候補ルールを抽出する（セッション終了後のコールドパス） |
| **shadow pool** | バックグラウンドで candidate/suppressed を照合するプール。エビデンスには使うが注入しない |
| **OpenAI-compatible** | API アドレスに `.../v1` を入れれば Ollama、LM Studio、OpenRouter 等に接続可能 |
