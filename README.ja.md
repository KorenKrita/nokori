# Nokori 残り

<p align="center">
  <img src="docs/assets/logo.png" width="160" height="160" alt="Nokori" />
</p>

<p align="center">
  <strong>Claude Code と Cursor のために鍛えあげた行動記憶層。</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/v/nokori" alt="PyPI" /></a>
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/pyversions/nokori" alt="Python" /></a>
  <a href="https://github.com/KorenKrita/nokori/blob/main/LICENSE"><img src="https://img.shields.io/github/license/KorenKrita/nokori" alt="License" /></a>
  <a href="https://github.com/KorenKrita/nokori/stargazers"><img src="https://img.shields.io/github/stars/KorenKrita/nokori" alt="Stars" /></a>
</p>

<p align="center">
  <b>Languages:</b> <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a> | <a href="README.zh-TW.md">繁體中文</a> | <b>日本語</b>
</p>

<p align="center">
  <a href="#クイックインストール">インストール</a> · <a href="#1分で理解する">仕組み</a> · <a href="docs/ja/architecture.md">アーキテクチャ</a> · <a href="docs/ja/configuration.md">設定</a> · <a href="docs/ja/cli.md">CLI リファレンス</a> · <a href="docs/ja/web-ui.md">Web UI</a>
</p>

---

> 経験が残すものは、記憶より深い。

残り（nokori）——騒がしさが過ぎ去ったあとも、その場にとどまっているもの。

対話が終わるたび、あなたが正した言葉は蒸発する。次のセッションで Agent はまた見知らぬ他人に戻る。平気で強制 push し、マイグレーションを流し忘れ、本番 DB に危険なコマンドを叩き込むあの他人に。

Nokori は忘れさせない。あなたの「こうするな」を呼び戻せる行動ルールとして沈殿させる。次にあなたの言葉があの場面に近づけば、ルールが自ずと Agent のコンテキストへ浮かび上がる。新しいルールはまず candidate として影に置かれ、コールドパスと事後エビデンスが信頼に足ると判断してから、もっとも鋭いものだけが Gate の資格を得る——Agent がファイルに触れる前に、最初の危険なツール呼び出しを一度止めるために。

データは終始あなたのマシン上の SQLite に残る。チャット中の検索はモデルに一切触れない。LLM を使うのはセッション終了後の抽出だけで、渡すのは圧縮済みの会話断片にすぎない。完全オフラインにしたければ、エンドポイントをローカルの Ollama に向ければいい。

---

## こんな人に向いている

- 同じ種類のミスを何度も正している人：強制 push、マイグレーション忘れ、間違った DB へのコマンド
- **プロジェクトをまたいで**「こうするな」を蓄えたい人。repo を開くたびに一から教え直すのはもう終わり
- ローカルを信頼する人：ルールは手元の SQLite に置かれ、いつでもエクスポートでき、会話全文が外に出ることはない

---

## 1分で理解する

```
あなたが Claude / Cursor を正す
    └─▶ Nokori が掟を1件刻む（どんな場面 + どうすべきか）
            └─▶ 次にあなたの言葉がその場面に近づく
                    └─▶ 掟が自ずと Agent のコンテキストへ書き込まれる（リマインド）
                            └─▶ やがて trusted + gate_eligible になれば：
                                 最初のファイル編集 / コマンド実行の前に、一度差し止める（Gate）
```

チャット中 Nokori がやるのは検索と小さなファイルの読み書きだけ。モデル待ちでブロックしない。LLM はセッション終了後、transcript（会話記録）から新ルールを抽出するときだけ動く。

---

## クイックインストール

**前提条件**：Python >= 3.11、Claude Code または Cursor がインストール済み

```bash
# 推奨：pipx でインストール（ローカル意味検索込み）
brew install pipx && pipx ensurepath
pipx install "nokori[local-embed]"

# Hook を登録
nokori install --all        # または --cursor / デフォルトは Claude Code のみ

# 動作確認
nokori health
```

<details>
<summary>その他のインストール方法</summary>

```bash
# 最小インストール（BM25 のみ、ローカルモデルなし）
pipx install nokori

# 専用 venv
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc

# ソースから
git clone https://github.com/KorenKrita/nokori.git && cd nokori
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
```

</details>

> 詳しいインストールガイド（Cursor 設定、更新、アンインストール等）は[インストール文書](docs/ja/installation.md)を参照

---

## クイックスタート

```bash
# 1. candidate ルールを追加
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --rationale "force push overwrites peers' work" \
  --source-type correction --confidence high

# 2. シャドウヒットを確認
nokori test "I'll just git push --force this branch"

# 3. メンテナンス実行（エビデンスに基づきルールを昇格）
nokori maintain

# 4. ルールが古くなったら退役
nokori dismiss <short_id>
```

普段どおり Claude Code / Cursor でコードを書けばよい。ルールにマッチすると、Agent の返信前にリマインドが注入される。`trusted` + `gate_eligible` のルールは、最初の敏感なツール呼び出しを一度差し止める。

---

## コア機能

| 機能 | 説明 |
|------|------|
| **自律品質フライホイール** | candidate → active → trusted。ルールはエビデンスを積まなければ強くなれない |
| **ホットパスで LLM 呼び出しゼロ** | Hook は決定的な検索・マッチ・スコアリングだけ。prompt と返信の間にモデル待ちなし |
| **ハイブリッド検索** | BM25 はすぐ使える + オプションのローカル/リモート意味ベクトル、RRF で融合 |
| **保守的な Gate** | trusted + gate_eligible のルールだけがツールを差し止め、しかも一度きり |
| **シャドウエビデンス** | Candidate はバックグラウンドで反事実エビデンスを蓄積。現在の対話には干渉しない |
| **ローカル優先** | SQLite + ファイルシステム。データは手元を離れず、オフライン LLM も選択可 |
| **クロスツール対応** | Claude Code と Cursor をネイティブにサポート |
| **Web UI** | `nokori web` 一つですべての状態を可視化管理 |

---

## ドキュメント

| 文書 | 内容 |
|------|------|
| [アーキテクチャ](docs/ja/architecture.md) | フライホイール機構、Hook タイミング、注入 vs Gate、Shadow Pool |
| [インストールガイド](docs/ja/installation.md) | 各プラットフォームのインストール、Cursor 設定、更新とアンインストール |
| [設定](docs/ja/configuration.md) | config.toml、環境変数の完全リファレンス |
| [検索エンジン](docs/ja/retrieval.md) | BM25、Embedding、注入の階層化 |
| [ルールのライフサイクル](docs/ja/lifecycle.md) | 状態機械、昇格条件、メンテナンスタスク |
| [自動抽出](docs/ja/extraction.md) | コールドパスパイプライン、マージ戦略、Async モード |
| [Gate 機構](docs/ja/gate.md) | 二層マッチ、設定、Prompt-hash 安全機構 |
| [CLI リファレンス](docs/ja/cli.md) | 全コマンドとオプション |
| [Web UI](docs/ja/web-ui.md) | 可視化パネルの機能と開発 |

---

## 既存システムとの関係

| システム | 関係 |
|----------|------|
| CLAUDE.md | 補完関係。Nokori は CLAUDE.md に触れない。動的な「Xに遭遇したらYする」を管理する |
| Claude Code auto-memory | 競合しない。memory は事実を記憶し、Nokori は行動規則を記憶する |
| 他の memory プラグイン | Hook は共存可能だが、コンテキストに注入する系のプラグインを重ねすぎないこと |

---

## データ保管

すべてのデータはローカルの `~/.nokori/` 一つのディレクトリに収まる。ネットワーク同期なし。ルールに含まれるのは行動の記述であり、ソースコードではない。LLM を使うのはコールドパスの抽出だけで、エンドポイントをローカル Ollama に向ければ完全オフライン運用が可能。

---

## 開発

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
python -m pytest tests/
```

プロジェクト制約：コアは純 stdlib + urllib、ホットパスで LLM 呼び出し禁止、すべての hook はトップレベル try/except で fail-open。

---

## License

MIT
