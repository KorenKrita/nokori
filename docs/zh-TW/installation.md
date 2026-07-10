# 安裝指南

[← 返回主文件](../../README.zh-TW.md)

---

## 開始之前

- **Python ≥ 3.11**（熱路徑 hook 僅使用 stdlib；基礎安裝包含 fastapi + uvicorn + websockets 用於 Web 儀表盤）
- 已裝好 **Claude Code** 或 **Cursor** 任意一個
- 想用本地語義檢索，預留約 **220MB** 磁碟裝嵌入模型權重（可選）

三種裝法，按需挑一種：本地模型（推薦）、最小安裝、從原始碼開發。

---

## macOS / Linux：別用系統 `pip` 直裝

Homebrew 等自帶的 Python 受 [PEP 668](https://peps.python.org/pep-0668/) 保護，直接 `pip install nokori` 會報 **`externally-managed-environment`**。請用 **pipx**（推薦）或 **專用 venv**，不要用 `--break-system-packages`。

### 方式 A：`pipx`（推薦，適合 CLI）

```bash
brew install pipx
pipx ensurepath
# 新開一個終端，或 source ~/.zshrc

pipx install "nokori[local-embed]"
nokori install --all        # 或 --cursor / 預設只裝 Claude Code
nokori health
```

`pipx` 把 `nokori` 裝進獨立環境，指令一般在 `~/.local/bin/nokori`；`nokori install` 會把 hook 寫成該環境的 `python -I -m nokori hook`。

### 方式 B：專用 venv

```bash
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install -U pip
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

nokori install --all
nokori health
```

---

## 從 PyPI 安裝（推薦：本地語義檢索）

這條路在本機跑語義檢索，不需要任何 embedding API key。它會裝上 **sentence-transformers**，並在 `nokori install` 時從 Hugging Face 預取本地嵌入模型 **[IBM Granite Embedding 97M](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)**（`ibm-granite/granite-embedding-97m-multilingual-r2`）到 `~/.nokori/models/`：**97M 參數 / 384 維**，下載約 **220MB**。

按上一節用 **pipx** 或 **venv** 安裝後：

```bash
# 註冊 hooks / bridge
nokori install              # Claude Code  → ~/.claude/settings.json
nokori install --cursor     # 僅原生 Cursor → ~/.cursor/hooks.json
nokori install --omp        # 僅 OMP         → ~/.omp/agent/extensions/nokori.ts
nokori install --all        # Claude + Cursor

# 驗證（安裝 OMP 時會顯示 hooks.omp）
nokori health
nokori status
nokori logs                 # hook / pipeline / async-extract 日誌
```

幾個常用旁支：

- **跳過權重下載**：`nokori install --no-prefetch-embed`
- **手動補下 / 重試**：`nokori embed prefetch`
- **偵錯 hook**：`config.toml` 裡設 `log_level = "info"`，或 `export NOKORI_LOG_LEVEL=info`

---

## 最小安裝（不要本地模型）

```bash
pipx install nokori
nokori install
```

開箱就有 BM25 關鍵詞檢索，夠用。想要語義檢索時，接任意 OpenAI 相容的 embedding API（設 `NOKORI_EMBED_BASE_URL`、`NOKORI_EMBED_MODEL`），或者哪天再補 `pip install "nokori[local-embed]"`。

---

## 從原始碼開發

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[local-embed,dev]"

nokori install
```

`nokori install` 把 hook **合併**進 `~/.claude/settings.json`，不碰你已經裝好的其它外掛。

```bash
# 預覽將要寫入的變更，不落盤
nokori install --dry-run

# 解除安裝（只摘掉 nokori 的 hooks）
nokori install --uninstall

# 臨時停用
nokori install --disable
nokori install --enable
```

---

## Claude Code、Cursor 與 OMP

預設裝 **Claude Code**；**Cursor** 保留原生 hook 與從 Claude 匯入兩條路；**OMP** 會安裝一個小型 TypeScript 橋接到 `~/.omp/agent/extensions/nokori.ts`，把執行時事件轉進 Nokori 既有的 Python dispatcher。同一台機器上請只選一種 Cursor 註冊方式。

### 裝哪條指令？

`--all` 仍然只代表 Claude Code + Cursor，不包含 OMP。

| 目標 | 指令 | 寫入 |
|------|------|------|
| 僅 Claude Code | `nokori install` | `~/.claude/settings.json` |
| 僅 Cursor（原生 `~/.cursor/hooks.json`） | `nokori install --cursor` | `~/.cursor/hooks.json` |
| 僅 OMP | `nokori install --omp` | `~/.omp/agent/extensions/nokori.ts` |
| Claude Code + Cursor | `nokori install --all` | 上面兩個檔案 |
### 驗證 OMP 安裝

- 想先看會寫什麼，可先跑：`nokori install --omp --dry-run`
- 執行 `nokori health`，確認 `hooks.omp` 顯示 `ok registered`
- 開一個新的 OMP session。recall 會在 `before_agent_start` 注入，Gate 會在 `tool_call` 檢查，關會話後則由 `session_shutdown` 依 OMP session manager 提供的目前 session 檔案啟動提取。

### Cursor 只選一條路（不要混用）

| 路徑 | 怎麼做 | 適合 |
|------|--------|------|
| **A — 從 Claude 匯入** | `nokori install`，再在 Cursor：Settings → Hooks → 從 Claude Code 匯入 | 本來就用 Claude Code |
| **B — Cursor 原生** | 只跑 `nokori install --cursor`；不要再開 Claude 匯入 | 只要 Cursor |

**若兩套都生效**，同一條使用者訊息可能觸發 Nokori 兩次。預設開啟 **hook 合併**（`NOKORI_HOOK_COALESCE=1`）：只有第一次呼叫會跑檢索/Gate/提取，第二次空跑通過。`nokori health` 會在雙註冊時警告。

### 僅 Cursor 要注意的

- **終端工具名**：Cursor 用 `Shell`，Claude Code 用 `Bash`。`nokori install --cursor` 會在 preToolUse matcher 裡帶上 `Shell`。
- **Deferred 注入**：某輪若 Cursor 沒觸發 `beforeSubmitPrompt`，第一次匹配的 `preToolUse` 可能 deny 一次帶上規則。deny 後請再執行同一工具一次。

---

## 更新

```bash
# pipx
pipx upgrade nokori

# pip（venv 內）
pip install --upgrade nokori

# 從原始碼
git pull && pip install -e ".[local-embed,dev]"
```

升級後跑一下 `nokori health` 確認一切正常。Hook 註冊跨版本穩定，升級後不需要重新 `nokori install`。
