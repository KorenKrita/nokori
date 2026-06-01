# Nokori (残り)

**Languages:** [English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | **日本語**

> 経験が残す痕跡は、記憶よりも深い。

**Claude Code 向けのルール・ノートブック**——あなたが修正した言葉や踏んだ落とし穴を、次回自動で呼び出せる行動ルールとして蓄積する。

記録するのは「前回何を話したか」ではなく、「次回どうすべきか」：類似シーンで先に Claude に知らせ、必要なら**ツール呼び出しを一度だけブロック**し、ルールを読んでからコードを直させる。

---

## こんな人向け

- 同じ種類の問題（強制 push、マイグレーション忘れ、危険なコマンド）を何度も修正している人  
- **プロジェクト横断**で「こうしない」を蓄積し、repo ごとに一からやり直したくない人  
- ルールをローカル SQLite に保存・エクスポートでき、チャット全文を LLM に再送したくない人  

---

## 1分で理解

```
あなたが Claude を修正
    → Nokori がルールを1件記録（トリガー状況 + 取るべき行動）
    → 次回、あなたの発言がそのときに似ている
    → Claude のコンテキストに自動書き込み（リマインド）
    → 高危険な修正系で強くマッチした場合：最初のファイル編集・コマンド実行の前に一度ブロック（Gate）
```

**チャット中**は Nokori はできるだけ速く（検索 + ファイル、hook 内で LLM は呼ばない）；**セッション終了後**に LLM で transcript（セッション記録）から新しいルールを掘り出す。

---

## 用語早見表

初めて読むときに英語略語が出てきたら、この表を先にざっと見てください。後述でも重要概念は繰り返します。

| 用語 | 説明 |
|------|------|
| **hook**（フック） | Claude Code が決まったタイミングで自動実行する短いコマンド（例：メッセージ送信の前後） |
| **injection**（注入） | マッチしたルールを Claude がそのターンで見えるコンテキストに書き込むこと |
| **Gate**（ゲート） | 少数の「高危険な修正」系ルール向け：最初にマッチしたツール呼び出しを一度 **deny**（拒否）し、Claude にルールを読ませる |
| **marker**（マーカー） | そのターン用の「先に Gate ルールを読んで」という一時メモ。一度使ったら破棄 |
| **transcript** | Claude のセッション全体の `.jsonl` ログ。ルール自動抽出時に読む |
| **trigger / action** | ルールの2半分：「どんな状況で」+「どうすべきか」 |
| **short_id** | ルールの短い ID（例：`a3f2b1`）。dismiss や照合用 |
| **dismiss** | ルールを退役（検索・Gate の対象外にする） |
| **HOT / WARM** | マッチ度の段階：かなり関連 / やや関連。HOT ほど多く書き込む |
| **BM25** | キーワード重なりでスコア。GPU 不要、デフォルトで有効 |
| **embedding**（埋め込みベクトル） | 意味的類似度でスコア。ルールが増えたら任意で有効化 |
| **RRF** | BM25 ランキングとベクトルランキングを1つの総合ランキングに統合するアルゴリズム |
| **fail-open** | Nokori 自身がエラーでも **Claude を止めない**。そのターンは通知しない |
| **extract** | transcript から LLM で候補ルールを**抽出**（コールドパス、急がない） |
| **shadow pool**（シャドウプール） | 他プロジェクトのルール：「グローバル昇格すべきか」の統計にのみ使い、**現在の会話には注入しない** |
| **promotion**（昇格） | 複数の別プロジェクトで認められたプロジェクトルールが **global**（全体可視）になること |
| **candidate / active / dormant** | 確認待ち → 使用中 → 長期未使用で休眠 |
| **merged / archived** | 新ルールに置き換え / ユーザーまたはシステムによる無効化 |
| **supersede** | 新ルールが旧ルールを置き換える（旧は merged 状態に） |
| **OpenAI-compatible** | API URL に `.../v1` を指定すれば Ollama、LM Studio、OpenRouter 等に接続可能 |

---

## 仕組み

Nokori は Claude Code に **4 つの hook** を登録する。通常のチャット中、これらはローカル DB 検索・スコア計算・小さなファイル I/O のみ——**hook 内では LLM を呼ばない**（毎メッセージでモデル待ちになるため）。

| Hook | 説明 | レイテンシ予算 |
|------|------|----------|
| `SessionStart` | セッション開始：任意で前セッションの未抽出 user 末尾 + DB メンテナンス確認 | ≤ 1.5s |
| `UserPromptSubmit` | メッセージ送信ごと：ルール検索 → コンテキスト注入 → 必要なら Gate マーカー書き込み | ≤ 500ms |
| `PreToolUse` | Claude がツール使用前：マーカーがあれば **一度ブロック**、その後マーカー破棄 | ≤ 50ms |
| `SessionEnd` | セッション終了：「抽出待ち」ジョブファイルを記録。async モードならバックグラウンドで extract | ≤ 200ms |

2つの中核機能：

1. **リマインド（注入）** — マッチしたルールを HOT/WARM に応じて `additionalContext` に書き込み、Claude が返信前に見られる  
2. **一度ブロック（Gate）** — **correction / anti_pattern** で特に精度が高く、confidence が high で active なルールのみツールをブロック。**solution（解法系）はリマインドのみ、ブロックしない**（[注入 vs ブロック](#注入-vs-ブロック) 参照）

---

## インストール

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
pip install -e .

# 任意：ローカル embedding（sentence-transformers を入れ、~/.nokori/models/ に重みを自動ダウンロード）
pip install -e ".[local-embed]"

# Claude Code に hook を登録（[local-embed] 済みなら hook 変更の有無に関わらず prefetch）
nokori install
# 重みダウンロードをスキップ：nokori install --no-prefetch-embed
# 手動ダウンロード／再試行：nokori embed prefetch

# 確認
nokori health
nokori status
nokori logs          # hook / pipeline / async-extract ログ
```

`nokori install` は上記 hook を `~/.claude/settings.json` に**マージ**して書き込み、既存の他プラグインは上書きしない。`settings.json` が破損している（不正 JSON）場合、install は**書き込みを拒否して終了**（`nokori health` の settings 検証と同じ）。

```bash
# 書き込み前に変更をプレビュー
nokori install --dry-run

# アンインストール（nokori の hook のみ削除、他は保持）
nokori install --uninstall

# 一時無効化（hook は残るが実行しない）
nokori install --disable
nokori install --enable
```

### Cursor で使う（Claude Code hook インポート）

[README.zh-CN の Cursor 節](README.zh-CN.md#在-cursor-裡使用claude-code-hook-导入)と同様。`nokori install` はユーザーレベルの `~/.claude/settings.json` のみ。Cursor で Claude Code hook をインポートする場合、**プロジェクト経由でインポートされた hook はオフ**にし、ユーザーレベルの nokori だけにしてください。`nokori install` / `--dry-run` 実行後に注意が表示されます。

---

## クイックスタート

以下3ステップで Nokori を体感できます。詳細は後続セクション。

### 1. ルールを手動追加

```bash
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --rationale "force push overwrites peers' work" \
  --source-type correction \
  --confidence high \
  --variants "git push --force,git push -f" \
  --terms-zh "強制push,コード上書き"
```

`--project-id` を省略すると `project_scope=global`（全プロジェクトの正式プールで可視）。指定すると `project_scope=project` でその `project_id` に紐づく。

### 2. 検索をシミュレート（Claude 起動不要）

```bash
nokori test "I'll just git push --force this branch"
# デフォルト project_id = カレントの git ルート（hook と同じ）；--project で上書き可
```

出力：

```
prompt        "I'll just git push --force this branch"
candidates    1 rules in pool
bm25.matches  1

HOT  (1):
  abc123  rrf=0.0164  bm25=1.53  matched=['branch', 'force', 'git', 'push']
    Force pushing to a shared branch
WARM (0):

gate.would_block  True
  abc123: Use --force-with-lease, or push to a new branch
```

### 3. 実セッションで試す

いつも通り Claude Code でコーディングする。発言がルールに似ていると：

- Claude は**返信前**に注入されたルールを見る（HOT は詳しく、WARM は短く）  
- **correction / anti_pattern** で特に精度が高い場合：最初の Write / Bash 等が**一度ブロック**され、UI に理由と `short_id` が表示される  
- **同じユーザーメッセージ内**で一度ブロックされた後、以降のツール呼び出しは通る（マーカーは破棄済み）  
- **solution（解法系）** ルール：リマインドには出るが、**ツールはブロックしない**

### 4. ルールが古くなった？（Dismiss）

各ルールには **short_id**（例：`a3f2b1`）があり、注入テキストと Gate ブロック理由の両方に表示される。適用外になったルールは**退役**（`archived` 状態、検索・Gate 対象外）。

**方法1：ターミナル（いつでも）**

```bash
nokori dismiss a3f2b1
```

**方法2：会話で一言（Gate / 注入リマインドと併用）**

ルールが注入された直後、または Claude が Gate でブロックされたとき、プロンプトに `dismiss <short_id>` で退役できる旨が書かれる。**次のユーザーメッセージ**に：

```text
dismiss a3f2b1
```

`UserPromptSubmit` hook がこれを認識してルールをアーカイブする。

| 比較 | CLI `nokori dismiss` | 会話内 `dismiss <short_id>` |
|------|----------------------|-----------------------------|
| 時間制限 | **過去24時間以内**に注入されたことがある（任意の session） | **過去24時間以内**に注入；通常 `session_id` は現在 session に限定、`session_id` が `-` のときは CLI と同じ（任意 session） |
| 動詞 | 固定サブコマンド | 設定可能。`dismiss_phrase` 参照（デフォルト `dismiss`） |

`dismiss_phrase` を `forget` に変更した場合、会話では `forget a3f2b1`（`nokori dismiss` サブコマンド名は変わらない）。形式は固定：**1語 + スペース + short_id**。自然文全体ではない。

設定：`dismiss_phrase` / `NOKORI_DISMISS_PHRASE`。[設定ファイル](#設定ファイル) と [config.toml.example](config.toml.example) 参照。

---

## Gate と PreToolUse：2段階の「ツールマッチ」

> **Gate とは？** 常時ミュートではなく、「このターン、危険なツールを初めて触る前に Claude にルールを見せる」。ブロック後はマーカーを破棄し、同じメッセージ内の以降は通常通り。

多くの人は「Gate がツールをブロックする」スイッチが1つだと思いがちだが、実際は**2段階**で、設定場所も内容も異なる：

```
Claude がツールを呼び出そうとする
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第1層：Claude Code settings.json の PreToolUse.matcher   │
│ 「nokori hook pre-tool-use を実行するか」                  │
│ デフォルト：Edit|Write|MultiEdit|Bash|NotebookEdit        │
│ Read / Grep 等はデフォルトで hook に入らない              │
└─────────────────────────────────────────────────────────┘
    │ hook 実行済み
    ▼
┌─────────────────────────────────────────────────────────┐
│ 第2層：Nokori [gate].matcher（NOKORI_GATE_MATCHER）        │
│ 「hook 内でこの tool_name を block するか」               │
│ デフォルト：同上；Python 正規表現、payload.tool_name を fullmatch │
└─────────────────────────────────────────────────────────┘
    │ marker ありかつマッチ
    ▼
  一度 deny → marker 削除 → 同ツール再試行で許可
```

Gate ブロック時、hook は Claude Code 公式形式を返す（[Hooks reference — PreToolUse](https://code.claude.com/docs/en/hooks)）：`hookSpecificOutput.permissionDecision: "deny"` と `permissionDecisionReason`（Claude に表示）。トップレベルの `decision`/`reason` はこのイベントでは非推奨のため、Nokori は出力しない。

### 第1段階：どのツールで hook を実行するか

- **設定ファイル**：`~/.claude/settings.json`（`nokori install` が書き込み、`config.toml` は読まない）
- **フィールド**：`hooks.PreToolUse` 内の nokori エントリの `matcher`
- **デフォルト**（install 時）：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **「任意のツールで hook を実行」**：該当エントリの `matcher` を `*` に（Claude Code 規約、全 PreToolUse イベントを意味）

例（nokori エントリのみ。他の hook は保持）：

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

既にインストール済みの場合は settings を**手動編集**するか、`nokori install --uninstall` 後に再 `install`（リポジトリ内デフォルト matcher で書き戻し、`*` ではない）。変更後 `config.toml` の修正は不要。

### 第2段階：hook 内でどの tool_name を実際に block するか

- **設定ファイル**：`~/.nokori/config.toml` の `[gate] matcher`、または環境変数 `NOKORI_GATE_MATCHER`
- **意味**：hook が呼ばれた後、payload の `tool_name` を **Python `re.fullmatch`** でマッチ
- **デフォルト**：`Edit|Write|MultiEdit|Bash|NotebookEdit`
- **「hook に入ったツールすべてで block 判定」**：`.*` を設定（**リテラル `*` は不可**。正規表現では無効）

```toml
[gate]
matcher = ".*"
```

この段階だけ変更し settings を変えない場合：Read 等は**hook に入らない**ため block もされない。「任意ツールが Gate 対象」にするには両段階を変更する必要がある。

### 注入 vs ブロック

| | 注入（`additionalContext`） | Gate（PreToolUse deny） |
|--|------------------------------|-------------------------|
| ルール範囲 | 正式プール HOT + WARM | 正式プール HOT の部分集合 |
| `source_type` | すべて（solution、preference 含む） | **correction**、**anti_pattern** のみ |
| その他条件 | 検索階層の基準を満たす | かつ **high** + **active** |

例：`solution` ルールは HOT リマインドに出るが、Gate では最初の Write/Bash を**ブロックしない**。

### その他 Gate 関連設定

| 項目 | 作用 |
|----|------|
| `[gate] enabled` / `NOKORI_GATE_ENABLED` | 総スイッチ。オフなら注入のみ、block なし |
| `[gate] ttl_seconds` / `NOKORI_GATE_TTL_SECONDS` | マーカー有効期限（デフォルト 600s）。期限切れで block しない。**`0` は無期限** |

**Prompt-hash 不一致（fail-open）**：`UserPromptSubmit` がマーカー書き込み時に現在 prompt の hash を記録；`PreToolUse` は payload または当 session の直近 `injections.prompt_hash` から現在 hash を解決（ディスク上の「最新マーカーファイル」を現在ターンの代用には**しない**）。解決不能またはマーカーと不一致（ユーザーが次メッセージ送信済み）の場合、**マーカーを削除してツールを通す**。block しない。

---

## 自動抽出

セッション終了後の「ゆっくりした作業」：LLM 設定後、Nokori は Claude Code の **transcript**（`.jsonl` セッション記録）を読み、修正内容を候補ルールにまとめ、既存ルールとマージする。

```bash
# LLM 設定（任意の OpenAI-compatible エンドポイント）
export NOKORI_LLM_BASE_URL="http://localhost:11434/v1"
export NOKORI_LLM_MODEL="qwen2.5:7b"

# 手動抽出（transcript 指定；project は SessionEnd job の project_id を優先）
nokori extract --session ~/.claude/projects/.../session.jsonl
nokori extract --session .../session.jsonl --project myrepo-a1b2c3d4

# dry-run プレビュー
nokori extract --session ~/.claude/projects/.../session.jsonl --dry-run

# 保留中の extract jobs をすべて処理
nokori extract
```

抽出フロー：transcript 読み込み（単一ファイル ≤ 50MB）→ 圧縮（ユーザーメッセージ保持、AI 応答は切り詰め）→ LLM で候補ルール抽出 → 既存ルールとマージ（SAME/BROADER/CONTRADICTS/UNRELATED）。

**LLM 呼び出し方式**：抽出と merge は **system**（固定指示）+ **user**（信頼できない本文）の2メッセージ。transcript / 候補 / 既存ルール本文は `--- BEGIN UNTRUSTED DATA ---` 区切りブロック内に包み、ツール出力に混ざった対抗指示の影響を低減。リモートエンドポイントは OpenAI-compatible `/v1/chat/completions`。未設定時は `claude -p` にフォールバック（system は `--system-prompt`、本文は stdin）。

**Merge 判定（実装）** — LLM 関係文字 `A`–`E` は SAME / BROADER / NARROWER / CONTRADICTS / UNRELATED に対応：

| 判定 | 動作 |
|------|------|
| **SAME (A)** + 既存 `candidate` | evidence 追加；high correction なら即 activate、それ以外は evidence ルールで activate |
| **SAME (A)** + 既存 `active` / `dormant` | **新規ルール作成しない**；既存行に `add_evidence(..., "same_extraction", 1)`、全履歴保持 |
| **BROADER / CONTRADICTS (B/D)** | 新ルール挿入し旧ルールを `supersede`；同ラウンドで別行に **A** 判定済みなら A 行へ `supersede`、2つ目の active は挿入しない |
| **NARROWER (C)** | 新ルール挿入（既存と共存）；同ラウンドに **SAME (A)** があっても本条候補は挿入 |
| **UNRELATED (E)** | 新 `candidate` 挿入、隣接ルールと独立 |
| 強い関係なし | 新 `candidate` 挿入 |

**Merge LLM 失敗**（隣接ルールありで関係 JSON が無効/タイムアウト）：**現在の候補**は独立ルールとして挿入されるが `merge_ok=false`、`nokori extract` は transcript を抽出済みと**マークしない**。job は **pending のまま**（checkpoint で処理済み候補を保持）再試行可能。

**抽出 LLM 失敗**（または非 JSON）：候補は**挿入されない**；job は **pending のまま**。

**近傍バックフィル（v0.1 意図的に維持）**：BM25 事前スクリーニングが5件未満のとき、`updated_at` が新しいルールを追加して LLM に送る。token 消費増・UNRELATED 多発の可能性——「ゼロ語彙重なり」による merge 漏れ防止用。スイッチなし。トレードオフ：LLM 呼び出しは増やしても、SAME/B/D の merge 漏れは避ける。

LLM 未設定時、Nokori は `claude -p --model haiku` を fallback として試行（prompt は stdin、argv には入れない）。

---

## データベース

- SQLite `rules.db`、初回使用時に自動作成
- DB が現在の nokori バージョンと非互換の場合エラー。先に `nokori export` でバックアップ、または新しい `NOKORI_DATA_DIR` / `nokori reset`

## ルールのライフサイクル

> 状態名は英語。[用語早見表](#用語早見表) 参照。以下は細かく調整したい人向け。

```
candidate（確認待ち）→ active（使用中）→ dormant（休眠）→ 再アクティブ化または archived（無効化）
                              ↘ merged（新ルールに置き換え）
```

| 状態 | リマインド対象？ | Gate 対象？ | 由来 |
|------|----------------|--------------|----------|
| `candidate` | いいえ | いいえ | 自動抽出、confidence 一般、まず観察 |
| `active` | はい | HOT かつ型が合えば可 | 手動 high correction、または evidence 十分で自動昇格 |
| `dormant` | はい（マッチ時 WARM 上限） | いいえ | 30日間「強関連」未使用（`last_hit` 参照） |
| `merged` | いいえ | いいえ | 新ルールに置き換え |
| `archived` | いいえ | いいえ | dismiss、または candidate 長期放置でクリーンアップ |

### アクティベーション条件

- **手動 `nokori add`** または **抽出マージ時**：`high` + `correction` 候補 → 直接 `active`（初期 `user_correction` evidence 含む）
- 純 AI evidence（クロスプロジェクト `shadow_hot` 含む）：`evidence_score >= 2` かつ `>= 2` 活動日

**`last_hit` の意味**：dormant スキャン用（`last_hit` 欠落時は `created_at`）。以下で更新：**(1)** 正式プール HOT/WARM が**実際にコンテキストへ書き込まれた**注入；**(2)** dormant ルールが検索基準を満たし当ターン再アクティブ化。`hit_count` は HOT 注入のみ +1。

**Dormant 再アクティブ化**：検索スコアが HOT 段階に達しても、**当ターン**は WARM 注入（gate なし）；DB は**当ターン**で `status=active` と `last_hit` 更新、**次ターン**から HOT + gate（correction/anti_pattern の場合）。`UserPromptSubmit` hook 動作と一致。

### Project ID

Nokori は `git rev-parse --show-toplevel` でプロジェクトルートを解決し、`<ディレクトリ名>-<パスhash先頭8桁>` を project_id とする。パスが異なる同名 repo は衝突しない。非 git ディレクトリは cwd パス hash にフォールバック。

### Global Promotion

各 `UserPromptSubmit` で**正式プール ∪ シャドウプール**を1回検索（BM25 + 任意 embedding RRF）、プール分割：正式プール HOT/WARM のみ注入；シャドウプールは **HOT と WARM** とも `record_shadow_hit`（promotion のみ、現在の会話には注入しない）。**≥3 個の異なる project_id** ヒットで `global` 昇格（**二次確認なし**、v0.1 の製品判断）。`preference` は対象外。

### Shadow Pool（シャドウプール）

**説明**：プロジェクト A でコーディング中、プロジェクト B で検証済みのルールも**スコア計算**に参加するが、**A の会話には入れない**——「このルールをグローバルに昇格すべきか」の判断材料のみ。

- 現在プロジェクトのルールと同じ検索（BM25、ルールが多ければ embedding + RRF）  
- **HOT または WARM** 到達で「シャドウヒット」を1回記録（promotion evidence）  
- **各「別プロジェクト × 当日」最大1回**（同日同プロジェクトの重複ヒットは加点しない）  
- **≥3 個の異なるプロジェクト**でヒット → ルールが `global`（全体）に。確認操作不要  

新プロジェクトでルールゼロでも promotion 有効ならシャドウプールは動く——ゼロからクロスプロジェクト合意を蓄積。無効化：`NOKORI_PROMOTION_ENABLED=0`。

進捗：`nokori status` で `shadow_hits` と `N/3 projects=...` を確認。

### Async Extract Mode（セッション終了後の自動抽出）

```bash
export NOKORI_EXTRACT_MODE=async
```

- **`manual`（デフォルト）**：セッション終了時に待機ファイルのみ作成。自分で `nokori extract` を実行  
- **`async`**：セッション終了時に可能ならバックグラウンドで extract（既にプロセス実行中ならキューに積むだけ、重複起動しない）  

ログ：`~/.nokori/logs/async-extract.log`。LLM 未設定時はローカル `claude -p` を試行。

`{data_dir}/extract.lock` が占有中（別インスタンスが extract 実行中、または異常残留）の場合、SessionEnd は子プロセスを**自動 spawn しない**。pending job は保持され、後で手動 `nokori extract` が必要。

SessionEnd 後も transcript が追記される（ファイル `mtime` 変化）場合、`nokori extract` は**job の mtime を更新して pending を維持**。job を黙って破棄しない。

破損した `extract-*.json`（パース不可）は `list_jobs` / `nokori extract` / `SessionStart` メンテナンス時に `{data_dir}/jobs/bad/` へ移動。ゾンビ job がディレクトリを占有しないようにする。

任意：`NOKORI_EXTRACT_DEFER_ACTIVE=1` のとき、async モードで**他に未 SessionEnd の session** がある（`active_sessions/` で `ended_at` が空、`count_open_sessions`）場合、現在の SessionEnd は**job のみ書き込み、`nokori extract` は fork しない**。他 session 終了後に手動または次 SessionEnd で抽出。

`NOKORI_SESSION_IDLE_SECONDS`（`[session] idle_seconds`）は defer に**関与しない**。`nokori status` の「active」表示のみ（open + 直近 `touch` ハートビート）。

Extract jobs は `nokori extract`（手動または async 子プロセス）が消費。**`async` モードの SessionStart** で pending job があり extract ロックが空なら、**バックグラウンドで extract spawn を再試行**。`nokori extract` は `{data_dir}/extract.lock`（Unix / Windows 対応）で並行重複処理を防止。既に実行中なら **exit 2** で `(extract already running)` を出力（「pending job なし」の exit 0 と区別）。

### ホットキャッシュ

SessionStart が「前セッション transcript」を探す：

1. **優先**：`{data_dir}/transcript_index/`（SessionEnd が書いた previous/current ポインタ）——**そのディレクトリで正常終了した直前 session** のファイル。必ずしも mtime 最大のより古い `*.jsonl` ではない。
2. **フォールバック**：同ディレクトリで mtime が現在ファイルより厳密に古い最新 `*.jsonl`（ヒューリスティック、最大50ファイルスキャン）。

前セッションが未 extract の場合、ファイル**末尾**から最後3件の user メッセージを注入（500 chars、独立予算）。**Dormant 疑似 HOT、shadow カウント、HOT の `hit_count`** はすべて **UserPromptSubmit 当ターン**に DB 書き込み。次 SessionStart まで待たない。

**Shadow と candidate アクティベーション**：クロスプロジェクト shadow HOT は `add_evidence(..., shadow_hot, 1)`。他プロジェクトのルールがまだ `candidate` なら、複数回（異なる日）の shadow ヒットで純 AI アクティベーション条件（score≥2 かつ2活動日）を満たす可能性——**「promotion のみ」の直感と異なるが、v0.1 では意図的に**クロスプロジェクト検索 evidence でのアクティベーションを許可。

### メンテナンス

メンテナンスタスクは `SessionStart` で自動トリガー（間隔チェック）：

- **Dormant スキャン**（7日ごと）：30日未ヒットの active → dormant
- **Candidate クリーンアップ**（スキャン間隔最大30日に1回）：**created_at ≥20 暦日**の通常 candidate、**≥40 日**の `anti_pattern` candidate を削除（「30日生存」ではない）
- **Unmerge チェック**（最大90日に1回）：`status=merged` で `superseded_by` 先が削除または dormant/archived なら `dormant` に復帰；**candidate クリーンアップでアンカールール削除後**も即 orphan unmerge
- **Session ファイルクリーンアップ**：`active_sessions/` で終了から60日超の registry ファイルを削除
- **Injection クリーンアップ**（スキャン間隔最大7日に1回）：**30日前**の `injections` 行を削除（dismiss は24h のみ参照、バッファ確保）

手動トリガーも可能：

```bash
nokori maintain
```

---

## 検索エンジン

> **関連ルールの見つけ方？** まずキーワード（BM25）、ルールが増えたら意味ベクトル、最後に RRF で2ランキングを統合。HOT/WARM 段階でコンテキストへの書き込み量を決める。

### BM25（デフォルト、依存ゼロ）

- ドキュメントフィールド：`trigger_text`、`trigger_variants`、`search_terms`、**`action`**
- Latin text: lowercase word tokens（≥ 2 chars）
- CJK テキスト：主に bigram；1文字 CJK は unigram も保持（recall 向上）
- 混合テキストは自動切り替え

### Embedding（埋め込みベクトル、任意）

ルール **≥ 20 件**（当該 prompt で検索するバッチ）かつリモート API 設定または `pip install nokori[local-embed]` 済みなら、自動で意味検索を追加。  
`NOKORI_EMBED_ENABLED=1` で強制試行（小規模 DB でも初回は BM25 のみの可能性、下記参照）。

**2種類の閾値（混同しやすい）**：

| シナリオ | カウント範囲 | 作用 |
|------|----------|------|
| **SessionStart** `embed` kickstart | 全 DB `active+dormant` 件数 | バックグラウンドで embed server を起動するか（≥20 で spawn 可能。現在プロジェクトのルール数とは無関係） |
| **UserPromptSubmit** 検索 | 当回 formal∪shadow プールサイズ | 当該 prompt で embedding RRF を使うか |

**半インデックス**：embed 有効後、**`rule_embeddings` 行がない**ルールは RRF 内 BM25 のみ（直後 activate、import 後未インデックス、インデックス失敗時）。意味検索は**現在設定の embed モデル名**と一致する `rule_embeddings` 行のみ使用。モデル・次元変更後は `reindex` / 再 `add` または `import` でインデックス再構築。`nokori health` の `embed.index` が欠落件数を warn。リモートエンドポイントのプローブは **HTTP 2xx** のみ ok（401/404 は非健全）。

リモート API モード：

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
# NOKORI_EMBED_DIMENSIONS はデフォルト未指定（モデル自身の次元）；OpenAI text-embedding-3 等のみ設定
```

ローカルモデルモード（URL 設定不要）：

```bash
pip install nokori[local-embed]
# 開発インストール：pip install -e ".[local-embed]"
```

`[local-embed]` インストール時に **sentence-transformers>=3.0** を入れる（Granite の `encode_query` / `encode_document` に必須。ST 2.x 非対応）。**モデル重み**（`ibm-granite/granite-embedding-97m-multilingual-r2`、約97Mパラメータ、384次元）は以下のタイミングで `~/.nokori/models/` にダウンロード（hook 内ではダウンロードしない。タイムアウト回避）。ユーザープロンプトは `encode_query`、ルール索引は `encode_document`（Granite R2 検索 API）。旧デフォルトモデルからの移行後は `nokori embed prefetch` を実行し、ルールを再インデックス（`add` / `import` / trigger 関連フィールドの編集）して `rule_embeddings` の `model_version` を新モデルに合わせてください：

| タイミング | 説明 |
|------|------|
| `pip install …[local-embed]` | パッケージインストール後に自動 prefetch（`pip install -e` も同様） |
| `nokori install` | `[local-embed]` 済みなら prefetch。**hook 登録済みかどうかは無関係** |
| `nokori embed prefetch` | 手動ダウンロードまたは失敗再試行 |

リモート embed endpoint 未設定かつ検索可能ルール ≥ 20 のとき、**embed 共有プロセス**が上記ディレクトリからモデルをロード。

Hook 動作（`NOKORI_EMBED_SERVER_AUTO_START=1`、デフォルトオン）：

- **SessionStart**：ローカル重みがキャッシュディレクトリにあれば → 非ブロッキング `spawn` embed server；**重み欠落はログのみ**、ブロックせず、hook 内で `import sentence_transformers` しない
- **UserPromptSubmit**：server がまだ `ping` 成功していなければ → バックグラウンド spawn、**当ターンは BM25 のみ**；次ターンから通常 RRF
- hook 内でモデルダウンロードや長時間ロードを待たない（Claude hook タイムアウト超過回避）

`nokori embed start` で事前起動可能；`NOKORI_EMBED_ENABLED=1` で embed 強制試行（ルール <20 でも）。小規模 DB では初回 BM25 のみの可能性あり。

優先順位：リモート API（base_url 設定）> ローカル embed server（`[local-embed]` 済み）> 純 BM25。server 未準備時は BM25 にフォールバック。各 hook 子プロセスでモデルを再ロードしない。

2種類のスコアは **RRF**（ランキング融合）で総合ランキングに合成し、HOT/WARM に分割。

**プラットフォーム**：ローカル embed は **macOS / Linux** のみ（`embed.sock`）。Windows は純 BM25 またはリモート `NOKORI_EMBED_BASE_URL`。

ローカル embed 管理（Unix）：

```bash
nokori embed prefetch # ローカルモデル重みをダウンロード（pip/install 済みなら省略可）
nokori embed start    # 共有 server をバックグラウンド起動（hook も必要時に自動 start）
nokori embed status   # プロセス / socket / idle 設定
nokori embed stop     # グレースフル終了（SIGTERM + IPC shutdown）
# nokori embed serve  # フォアグラウンドデバッグ；NOKORI_EMBED_SERVER_IDLE 秒アイドルで自動終了
```

ローカル embed server の Unix socket は `NOKORI_DATA_DIR` 下。**IPC 認証なし**（ローカル単一ユーザー想定。データディレクトリをマルチユーザー共有パスに置かないこと）。

### 注入階層

| 階層 | 条件 | 注入内容 |
|------|------|----------|
| HOT | top-1 かつ top-2 より有意に高い + 最低 evidence 通過；**1件のみヒット**時は `rrf_score > 0.01` かつ ≥3 matched token | trigger + action + rationale |
| WARM | top-5 内の残り（最低 evidence 含む） | trigger + action 1行 |
| COLD | top-5 外 | 注入しない |

**最低 evidence**：≥2 query token 重なり；または 1 token + trigger variant ヒット；または embedding cosine ≥ 0.55。純 embedding ヒット時 `matched_tokens` は空の可能性あり（cosine 閾値で HOT/WARM 進入可）。

注入予算：1500 chars（ルール）+ 500 chars（ホットキャッシュ、独立）。**実際にコンテキストへ書き込まれた**ルールのみ `injections` に記録し `last_hit` / HOT の `hit_count` を更新（予算切り詰めで書き込まれなかった分は記録しない）。

---

## CLI 完全リファレンス

```bash
# ルール管理
nokori add [--trigger "..." --action "..." --source-type ... --confidence ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]

# 抽出
nokori extract [--session <path>] [--dry-run]

# デバッグ
nokori test "<prompt>" [--project <id>]
nokori status          # promotion 進捗：各 project ルール N/3 異なる project で shadow HOT
nokori logs
nokori health

# メンテナンス
nokori maintain
nokori reset [--force]   # 非対話端末では --force 必須

# ローカル embed 共有プロセス（Unix；任意）
nokori embed prefetch | start | stop | status

# インポート／エクスポート（JSON の version = rules.db schema、現在 2）
nokori export <path.json>
nokori import <path.json>

# インストール
nokori install [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

---

## 環境変数

| 変数 | デフォルト | 説明 |
|------|--------|------|
| `NOKORI_DATA_DIR` | `~/.nokori` | データルート |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | 注入文字数上限 |
| `NOKORI_GATE_ENABLED` | `1` | gate 有効 |
| `NOKORI_GATE_TTL_SECONDS` | `600` | マーカー有効期限；`0` = 無期限 |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | **第2段階**：hook 内 block 対象 `tool_name` 正規表現（任意ツールは `.*`）；[Gate 2段階マッチ](#gate-と-pretooluse2段階のツールマッチ) 参照 |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` 時 async モードで active session ありなら extract fork を延期 |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | `active_sessions` でこの秒数ハートビートなしは非アクティブ |
| `NOKORI_HOT_CACHE` | `1` | SessionStart ホットキャッシュ |
| `NOKORI_PROMOTION_ENABLED` | `1` | シャドウプールとクロスプロジェクト promotion；`0` でシナリオ C 無効 |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | hook リモート embed タイムアウト（秒） |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | ローカル embed プロセスアイドル終了（秒） |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | hook が必要に応じ embed server を自動起動 |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions エンドポイント |
| `NOKORI_LLM_MODEL` | — | LLM モデル名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_EMBED_ENABLED` | `0`（active+dormant≥20 で自動） | embedding 強制有効 |
| `NOKORI_EMBED_BASE_URL` | — | OpenAI-compatible embeddings エンドポイント |
| `NOKORI_EMBED_MODEL` | — | Embedding モデル名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0`（未指定、モデルデフォルト） | ベクトル次元（パラメータ対応モデルのみ設定） |
| `NOKORI_EMBED_CHUNK_SIZE` | `4000` | テキストチャンク文字数 |
| `NOKORI_EMBED_CHUNK_COUNT` | `2` | ルールあたり最大チャンク数 |
| `NOKORI_STRICT` | `0` | `1` 時 hook 例外を再送出（デバッグ；デフォルト fail-open） |
| `NOKORI_DISABLED` | `0` | 完全無効 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | 会話内ルール退役の動詞（`動詞 + short_id`）；[Dismiss](#4-ルールが古くなったdismiss) 参照 |
| `NOKORI_LOG_LEVEL` | `warn` | ログレベル |

**環境変数のみ**（`config.toml` フィールドなし。[config.toml.example](config.toml.example) 参照）：

| 変数 | デフォルト | 説明 |
|------|--------|------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | `nokori install` が読み書きする `settings.json` ディレクトリ |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | transcript 読み取り追加許可ルート。`os.pathsep` 区切り（パス安全検証） |
| `NOKORI_EXTRACTING` | — | 内部：`claude -p` fallback 子プロセスの再帰防止。ユーザーシェルや async extract では設定しない |

すべての LLM/Embedding エンドポイント互換：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任意の `/v1/chat/completions` + `/v1/embeddings` エンドポイント。

---

## 設定ファイル

環境変数に加え、Nokori は TOML 設定ファイル `~/.nokori/config.toml` をサポート（パスは `NOKORI_DATA_DIR` に従う）。

リポジトリルートに完全テンプレート **[config.toml.example](config.toml.example)**（全設定項目、デフォルト、選択肢、説明）。

**優先順位**：環境変数 > config.toml > 組み込みデフォルト。

```toml
# ~/.nokori/config.toml

log_level = "info"
dismiss_phrase = "dismiss"

[llm]
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-v4-flash"
api_key = "sk-xxx"

[embed]
# リモート OpenAI-compatible API（下の server パラメータと同じ [embed] 表；[embed] 見出しを二重に書かない）
base_url = "https://api.example.com/v1"
model = "text-embedding-v4"
api_key = "sk-xxx"
# dimensions = 0  # 未設定または 0 = API に渡さない（モデルデフォルト次元）
chunk_size = 4000
chunk_count = 2
enabled = true
# ローカル embed 共有プロセス（base_url 未設定かつ pip install nokori[local-embed] 時）
# hook_timeout_seconds = 2
# server_idle_seconds = 3600
# server_auto_start = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
# defer_when_active = false   # 他に open session があるとき async extract を延期

[hot_cache]
enabled = true

[promotion]
enabled = true

[session]
# idle_seconds = 1800
```

全フィールドは環境変数と1対1対応（[config.toml.example](config.toml.example) 早見表参照）。ファイル不存在時は黙って無視。環境変数のみモードも動作。

**注意**：`[gate] matcher` は Nokori hook **内部**の block のみ影響。PreToolUse **が hook を呼ぶか**は `~/.claude/settings.json` が決定。[Gate 2段階マッチ](#gate-と-pretooluse2段階のツールマッチ) 参照。`dismiss_phrase` 詳細は [Dismiss](#4-ルールが古くなったdismiss)。

---

## データストレージ

すべてのデータはローカル `~/.nokori/` に保存：

```
~/.nokori/
├── config.toml           # 設定ファイル（任意、env vars 優先）
├── rules.db              # SQLite (WAL mode): ルール + インデックス + メタデータ
├── jobs/                 # Extract job キュー
├── active_sessions/      # Session registry
├── gate_markers/         # Gate markers（session + prompt_hash 単位）
├── logs/
│   ├── hook.log          # Hook プロセスログ
│   ├── pipeline.log      # 抽出／マージログ
│   ├── async-extract.log # async モード子プロセス stderr
│   └── embed-server.log  # ローカル embed server（有効時）
├── models/               # ローカル embed 重み（pip [local-embed] / install / embed prefetch）
├── embed.sock            # ローカル embed IPC（Unix）
└── extract.lock          # extract 単一インスタンスロック
```

- ネットワーク同期ゼロ、完全ローカル
- ルールにソースコードは含まない。行動記述のみ
- LLM 呼び出しは圧縮 transcript 断片を送信（ソースコードではない）
- ローカル Ollama 指定で完全オフライン可能
- **データベース**：現在の nokori バージョンにバインド。別マシンやアップグレード後に DB を開けない場合、`nokori export` でバックアップ、または新しい `NOKORI_DATA_DIR` / `nokori reset`。

---

## 既存システムとの関係

| システム | 関係 |
|------|------|
| CLAUDE.md | 補完。Nokori は CLAUDE.md を変更しない。ルールは動的な「X に遭遇したら Y する」 |
| Claude Code auto-memory | 競合しない。memory は事実寄り、Nokori は行動ルール寄り |
| その他 memory プラグイン | hook は共存可能。ただし「コンテキストに文字を詰める」プラグインの重ねすぎは非推奨 |

---

## 開発

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3.11+ -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/   # システムの python -m pytest は使わない（0 collected になり得る）
```

プロジェクト制約：
- ランタイム依存ゼロ（`dependencies = []`）
- 純 Python stdlib + urllib で API 呼び出し
- 対話ホットパス（UserPromptSubmit / PreToolUse）では LLM 呼び出し禁止
- すべての hook はトップレベル try/except、失敗時 pass-through

---

## License

MIT
