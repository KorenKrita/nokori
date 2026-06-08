# 設定

[← メインドキュメントへ戻る](../../README.ja.md)

---

## 設定ファイル

環境変数のほかに、Nokori は TOML 設定ファイル `~/.nokori/config.toml`（パスは `NOKORI_DATA_DIR` に従う）も読む。リポジトリルートに完全なテンプレート **[config.toml.example](../../config.toml.example)** がある。

**優先順位**：環境変数 > config.toml > 組み込みデフォルト。ファイルが存在しなければ黙って無視される。

### 目的別の設定項目

| やりたいこと | 変更する表 | 主なフィールド |
|--------|---------|---------|
| バックグラウンド抽出 / フォールバック LLM を設定 | `[llm]` | `base_url` `model` `api_key` |
| リモートまたはローカルの意味検索に接続 | `[embed]` | `base_url` `model` `enabled` |
| Gate がどのツールを、どの期間 block するか調整 | `[gate]` | `matcher` `ttl_seconds` `enabled` |
| セッション終了後の自動抽出タイミングを選択 | `[extract]` | `mode` `defer_when_active` |
| SessionStart ホットキャッシュの有効/無効 | `[hot_cache]` | `enabled` |
| Shadow pool ライフサイクルエビデンスの有効/無効 | `[promotion]` | `enabled` |
| Per-role LLM、max tokens、timeouts を調整 | `[models]`、`[models.limits]`、`[models.timeouts]` | テンプレート参照 |
| 会話内でルールを退役させる動詞を変更 | トップレベル | `dismiss_phrase` |

### config.toml テンプレート

```toml
# ~/.nokori/config.toml

log_level = "info"
dismiss_phrase = "dismiss"

[llm]
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-v4-flash"
api_key = "sk-xxx"

[embed]
base_url = "https://api.example.com/v1"
model = "text-embedding-v4"
api_key = "sk-xxx"
# dimensions = 0
chunk_size = 4000
chunk_count = 2
enabled = true
# server_idle_seconds = 3600
# server_auto_start = true

[gate]
enabled = true
ttl_seconds = 600
matcher = "Edit|Write|MultiEdit|Bash|NotebookEdit"

[extract]
mode = "manual"
# defer_when_active = false

[hot_cache]
enabled = true

[promotion]
enabled = true

[session]
# idle_seconds = 1800

[models]
# extractor = "deepseek-v4-flash"
# admission_judge = "deepseek-v4-flash"
# rule_rewriter = "deepseek-v4-flash"
# final_judge = "deepseek-v4-flash"
# merge_planner = "deepseek-v4-flash"
# synthetic_eval_generator = "deepseek-v4-flash"
# posthoc_evaluator = "deepseek-v4-flash"

[models.limits]
# extractor_max_tokens = 4000
# admission_judge_max_tokens = 2000
# rule_rewriter_max_tokens = 4000
# final_judge_max_tokens = 2000
# merge_planner_max_tokens = 3000
# synthetic_eval_generator_max_tokens = 4000
# posthoc_evaluator_max_tokens = 3000

[models.timeouts]
# extractor_timeout = 60
# admission_judge_timeout = 30
# rule_rewriter_timeout = 60
# final_judge_timeout = 30
# merge_planner_timeout = 45
# synthetic_eval_generator_timeout = 60
# posthoc_evaluator_timeout = 45
```

---

## 環境変数

| 変数 | デフォルト | 説明 |
|------|--------|------|
| `NOKORI_DATA_DIR` | `~/.nokori` | データルートディレクトリ |
| `NOKORI_MAX_INJECTION_CHARS` | `1500` | 注入文字数の上限 |
| `NOKORI_GATE_ENABLED` | `1` | Gate を有効化 |
| `NOKORI_GATE_TTL_SECONDS` | `600` | マーカー有効期限。`0` = 無期限 |
| `NOKORI_GATE_MATCHER` | `Edit\|Write\|MultiEdit\|Bash\|NotebookEdit` | hook 内で block する `tool_name` の正規表現 |
| `NOKORI_EXTRACT_MODE` | `manual` | `manual` / `async` |
| `NOKORI_EXTRACT_DEFER_ACTIVE` | `0` | `1` で async モード時に未終了セッションがあれば延期 |
| `NOKORI_SESSION_IDLE_SECONDS` | `1800` | この秒数ハートビートなしで非アクティブ扱い |
| `NOKORI_HOT_CACHE` | `1` | SessionStart ホットキャッシュ |
| `NOKORI_PROMOTION_ENABLED` | `1` | シャドウプールのライフサイクルエビデンス |
| `NOKORI_HOOK_EMBED_TIMEOUT` | `2` | Hook のリモート embed タイムアウト（秒） |
| `NOKORI_EMBED_SERVER_IDLE` | `3600` | ローカル embed プロセスのアイドル終了（秒） |
| `NOKORI_EMBED_SERVER_AUTO_START` | `1` | hook が必要時に embed server を自動起動 |
| `NOKORI_LLM_BASE_URL` | — | OpenAI-compatible chat completions エンドポイント |
| `NOKORI_LLM_MODEL` | — | LLM モデル名 |
| `NOKORI_LLM_API_KEY` | — | LLM API key |
| `NOKORI_MODEL_<ROLE>` | — | Per-role LLM オーバーライド |
| `NOKORI_EMBED_ENABLED` | `0`（>= 20 ルールで自動） | embedding を強制有効化 |
| `NOKORI_EMBED_BASE_URL` | — | Embeddings エンドポイント |
| `NOKORI_EMBED_MODEL` | — | Embedding モデル名 |
| `NOKORI_EMBED_API_KEY` | — | Embedding API key |
| `NOKORI_EMBED_DIMENSIONS` | `0` | ベクトル次元数 |
| `NOKORI_EMBED_CHUNK_SIZE` | `4000` | テキスト分割の文字数 |
| `NOKORI_EMBED_CHUNK_COUNT` | `2` | ルールあたりの最大チャンク数 |
| `NOKORI_STRICT` | `0` | `1` で hook 例外を上位に再送出 |
| `NOKORI_DISABLED` | `0` | 完全に無効化 |
| `NOKORI_HOOK_COALESCE` | `1` | 二重登録の重複排除 |
| `NOKORI_DISMISS_PHRASE` | `dismiss` | 会話内でルールを退役させる動詞 |
| `NOKORI_LOG_LEVEL` | `warn` | ログレベル |

**環境変数のみ**（config.toml フィールドなし）：

| 変数 | デフォルト | 説明 |
|------|--------|------|
| `NOKORI_CLAUDE_HOME` | `~/.claude` | install が読み書きする settings ディレクトリ |
| `NOKORI_TRANSCRIPT_EXTRA_ROOTS` | — | transcript 読み取りを追加許可するルートディレクトリ |
| `NOKORI_EXTRACTING` | — | 内部用：再帰防止マーカー |

すべての LLM/Embedding エンドポイント互換：Ollama、LMStudio、vLLM、OpenRouter、OpenAI、任意の `/v1` エンドポイント。

---

## データストレージ構造

```
~/.nokori/
├── config.toml           # 設定ファイル（オプション）
├── rules.db              # SQLite (WAL mode)
├── jobs/                 # Extract job キュー
├── active_sessions/      # Session registry
├── gate_markers/         # Gate marker
├── hook_coalesce/        # 二重登録の重複排除 claim
├── logs/
│   ├── hook.log
│   ├── pipeline.log
│   ├── async-extract.log
│   └── embed-server.log
├── models/               # ローカル embed ウェイト
├── embed.sock            # ローカル embed IPC（Unix）
└── extract.lock          # extract 単一インスタンスロック
```
