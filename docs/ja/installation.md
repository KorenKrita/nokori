# インストールガイド

[← メインドキュメントへ戻る](../../README.ja.md)

---

## はじめに

- **Python >= 3.11**（ホットパス hook は stdlib のみ使用。ベースインストールには Web ダッシュボード用の fastapi + uvicorn + websockets を含む）
- **Claude Code**、**Cursor**、**Pi**、または **OMP** のいずれかがインストール済み
- ローカル意味検索を使う場合、埋め込みモデルのウェイト用に約 **220MB** のディスクを確保（オプション）

インストール方法は 3 通り。用途に応じて一つ選ぶ：ローカルモデル（推奨）、最小インストール、ソースからの開発。

---

## macOS / Linux：システムの `pip` に直接入れない

Homebrew 等の Python は [PEP 668](https://peps.python.org/pep-0668/) で保護されており、直接 `pip install nokori` すると **`externally-managed-environment`** エラーになる。**uv tool**（推奨）、**pipx**、または**専用 venv** を使い、`--break-system-packages` は使わないこと。

### 方法 A：`uv tool`（推奨、CLI 向け）

```bash
# macOS。その他の環境は https://docs.astral.sh/uv/getting-started/installation/ を参照
brew install uv
uv tool install "nokori[local-embed]"

nokori install --pi         # Pi のみ。OMP は --omp、Claude + Cursor は --all
nokori health
```

`uv tool` は隔離環境を作成して `nokori` コマンドを公開し、システム Python は変更しない。Claude Code / Cursor はその環境の `python -I -m nokori hook` を呼び、Pi / OMP は生成済み TypeScript ブリッジから同じディスパッチャへ渡す。

### 方法 B：`pipx`

```bash
brew install pipx
pipx ensurepath
# 新しいターミナルを開く、または source ~/.zshrc

pipx install "nokori[local-embed]"
```

`pipx` も隔離された CLI 環境を使う、サポート済みの代替手段である。

### 方法 C：専用 venv

```bash
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install -U pip
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

nokori install --pi
nokori health
```

---

## PyPI からインストール（推奨：ローカル意味検索）

この方法ではマシン上で意味検索を走らせるため、embedding API key は不要。**sentence-transformers** を導入し、`nokori install` 時に Hugging Face からローカル埋め込みモデル **[IBM Granite Embedding 97M](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)**（`ibm-granite/granite-embedding-97m-multilingual-r2`）を `~/.nokori/models/` に prefetch する：**97M パラメータ / 384 次元**、ダウンロード約 **220MB**。

上記の **uv tool**、**pipx**、または **venv** でインストール後：

```bash
# Hook / bridge を登録
nokori install              # Claude Code  → ~/.claude/settings.json
nokori install --cursor     # Cursor ネイティブのみ → ~/.cursor/hooks.json
nokori install --pi         # Pi のみ → ~/.pi/agent/extensions/nokori.ts
nokori install --omp        # OMP のみ → ~/.omp/agent/extensions/nokori.ts
nokori install --all        # Claude + Cursor

# 動作確認（Pi / OMP 導入時は hooks.pi / hooks.omp が表示される）
nokori health
nokori status
```

よく使う補助操作：

- **ウェイトのダウンロードをスキップ**：`nokori install --no-prefetch-embed`
- **手動で補完 / リトライ**：`nokori embed prefetch`
- **Hook のデバッグ**：`config.toml` で `log_level = "info"`、または `export NOKORI_LOG_LEVEL=info`

---

## 最小インストール（ローカルモデルなし）

```bash
uv tool install nokori
nokori install
```

BM25 キーワード検索がすぐ使える。意味検索が欲しくなったら、OpenAI 互換の embedding API（`NOKORI_EMBED_BASE_URL`、`NOKORI_EMBED_MODEL` を設定）に接続するか、`uv tool install --force "nokori[local-embed]"` でローカルモデル依存込みに再インストールする。

---

## ソースから開発

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[local-embed,dev]"

nokori install
```

`nokori install` は hook を `~/.claude/settings.json` に**マージ**して書き込み、既存の他のプラグインには触れない。`nokori install --pi` / `--omp` は各ランタイム向け TypeScript ブリッジを書き込む。

```bash
# 書き込み予定の変更をプレビュー（ディスクには書かない）
nokori install --dry-run

# アンインストール（nokori の hooks だけ外す）
nokori install --uninstall

# 一時停止
nokori install --disable
nokori install --enable
```

---

## Claude Code・Cursor・Pi・OMP

デフォルトは **Claude Code**。**Cursor** はネイティブ hook または Claude からのインポートに対応。**Pi** と **OMP** はそれぞれ `~/.pi/agent/extensions/nokori.ts` と `~/.omp/agent/extensions/nokori.ts` に小さな TypeScript ブリッジを入れ、既存の Python ディスパッチャへランタイムイベントを渡す。

### どのコマンドで入れる？

`--all` は引き続き **Claude Code + Cursor** のみで、Pi と OMP は明示的に `--pi` / `--omp` を使う。

| 目的 | コマンド |
|------|------|
| Claude Code のみ | `nokori install` |
| Cursor のみ（ネイティブ `~/.cursor/hooks.json`） | `nokori install --cursor` |
| Pi のみ | `nokori install --pi` |
| OMP のみ | `nokori install --omp` |
| Claude Code + Cursor | `nokori install --all` |

### Pi / OMP の確認

- 必要なら先に `nokori install --pi --dry-run` または `nokori install --omp --dry-run` で書き込み内容を確認
- `nokori health` を実行し、`hooks.pi` または `hooks.omp` が `ok registered` と表示されることを確認
- 新しいセッションで、`before_agent_start` の注入・`tool_call` の Gate・`session_shutdown` 後の抽出が動くことを確認
- Pi の `/reload` ライフサイクルはブリッジが無視するため、現在のセッションを終了扱いしたり早期抽出したりしない
- `PI_CODING_AGENT_DIR` が設定されている場合、`nokori install --pi` と transcript 検証は `~/.pi/agent` ではなくそのディレクトリを使う

### Cursor は一本道だけ（混ぜない）

| 経路 | やり方 | 向いている人 |
|------|--------|------|
| **A — Claude からインポート** | `nokori install` した上で、Cursor：Settings → Hooks → Import from Claude Code | もともと Claude Code を使っている |
| **B — Cursor ネイティブ** | `nokori install --cursor` だけ。Claude インポートは開かない | Cursor だけ使う |

**両方が有効になってしまうと**、同じメッセージで Nokori が 2 回走りうる。デフォルトの **hook coalesce**（`NOKORI_HOOK_COALESCE=1`）が有効なので、最初の呼び出しだけが本処理を行い、2 回目は空パススルーになる。`nokori health` は二重登録を警告する。

### Cursor 固有の注意点

- **ターミナルツール名**：Cursor は `Shell`、Claude Code は `Bash`。`nokori install --cursor` は preToolUse matcher に `Shell` を含める。
- **Deferred 注入**：ある回で Cursor が `beforeSubmitPrompt` を発火しなかった場合、最初にマッチした `preToolUse` が一度 deny し、ルールを載せることがある。deny されたら同じツールを再実行すればよい。

---

## 更新

```bash
# uv tool
uv tool upgrade nokori

# pipx
pipx upgrade nokori

# pip（venv 内）
pip install --upgrade nokori

# ソースから
git pull && pip install -e ".[local-embed,dev]"
```

アップグレード後は `nokori health` で正常動作を確認する。Claude Code / Cursor の Hook 登録はバージョン間で安定している。`hooks.pi` または `hooks.omp` が生成済みブリッジを stale と報告した場合は、それぞれ `nokori install --pi` / `nokori install --omp` で更新する。
