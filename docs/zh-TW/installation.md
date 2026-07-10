# 安裝指南

[← 返回主文件](../../README.zh-TW.md)

---

## 開始之前

- **Python ≥ 3.11**（熱路徑 hook 僅使用 stdlib；基礎安裝包含 fastapi + uvicorn + websockets 用於 Web 儀表盤）
- 已裝好 **Claude Code**、**Cursor**、**Pi** 或 **OMP** 任意一個
- 想用本地語義檢索，預留約 **220MB** 磁碟裝嵌入模型權重（可選）

三種裝法，按需挑一種：本地模型（推薦）、最小安裝、從原始碼開發。

---

## macOS / Linux：別用系統 `pip` 直裝

Homebrew 等自帶的 Python 受 [PEP 668](https://peps.python.org/pep-0668/) 保護，直接 `pip install nokori` 會報 **`externally-managed-environment`**。請用 **uv tool**（推薦）、**pipx** 或 **專用 venv**，不要用 `--break-system-packages`。

### 方式 A：`uv tool`（推薦，適合 CLI）

```bash
# macOS；其它平台見 https://docs.astral.sh/uv/getting-started/installation/
brew install uv
uv tool install "nokori[local-embed]"

nokori install --pi         # 僅 Pi；OMP 用 --omp，Claude Code + Cursor 用 --all
nokori health
```

`uv tool` 會建立隔離環境並暴露 `nokori` 指令，不修改系統 Python；Claude Code / Cursor 會呼叫該環境的 `python -I -m nokori hook`，Pi / OMP 則透過產生的 TypeScript bridge 轉進同一個 dispatcher。

### 方式 B：`pipx`

```bash
brew install pipx
pipx ensurepath
# 新開一個終端，或 source ~/.zshrc

pipx install "nokori[local-embed]"
```

`pipx` 同樣使用隔離的 CLI 環境，可作為備選。

### 方式 C：專用 venv

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

## 從 PyPI 安裝（推薦：本地語義檢索）

這條路在本機跑語義檢索，不需要任何 embedding API key。它會裝上 **sentence-transformers**，並在 `nokori install` 時從 Hugging Face 預取本地嵌入模型 **[IBM Granite Embedding 97M](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)**（`ibm-granite/granite-embedding-97m-multilingual-r2`）到 `~/.nokori/models/`：**97M 參數 / 384 維**，下載約 **220MB**。

按上一節用 **uv tool**、**pipx** 或 **venv** 安裝後：

```bash
# 註冊 hooks / bridge
nokori install              # Claude Code  → ~/.claude/settings.json
nokori install --cursor     # 僅原生 Cursor → ~/.cursor/hooks.json
nokori install --pi         # 僅 Pi          → ~/.pi/agent/extensions/nokori.ts
nokori install --omp        # 僅 OMP         → ~/.omp/agent/extensions/nokori.ts
nokori install --all        # Claude + Cursor

# 驗證（安裝 Pi / OMP 時會顯示 hooks.pi / hooks.omp）
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
uv tool install nokori
nokori install
```

開箱就有 BM25 關鍵詞檢索，夠用。想要語義檢索時，可以接任意 OpenAI 相容的 embedding API（設 `NOKORI_EMBED_BASE_URL`、`NOKORI_EMBED_MODEL`），或用 `uv tool install --force "nokori[local-embed]"` 重新安裝並加入本地模型依賴。

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

## Claude Code、Cursor、Pi 與 OMP

預設裝 **Claude Code**；**Cursor** 保留原生 hook 與從 Claude 匯入兩條路；**Pi** 和 **OMP** 會分別在 `~/.pi/agent/extensions/nokori.ts` 與 `~/.omp/agent/extensions/nokori.ts` 安裝小型 TypeScript 橋接，把執行時事件轉進 Nokori 既有的 Python dispatcher。同一台機器上請只選一種 Cursor 註冊方式。

### 裝哪條指令？

`--all` 仍然只代表 Claude Code + Cursor，不包含 Pi 或 OMP。

| 目標 | 指令 | 寫入 |
|------|------|------|
| 僅 Claude Code | `nokori install` | `~/.claude/settings.json` |
| 僅 Cursor（原生 `~/.cursor/hooks.json`） | `nokori install --cursor` | `~/.cursor/hooks.json` |
| 僅 Pi | `nokori install --pi` | `~/.pi/agent/extensions/nokori.ts` |
| 僅 OMP | `nokori install --omp` | `~/.omp/agent/extensions/nokori.ts` |
| Claude Code + Cursor | `nokori install --all` | 上面兩個檔案 |
### 驗證 Pi / OMP 安裝

- 想先看會寫什麼，可先跑：`nokori install --pi --dry-run` 或 `nokori install --omp --dry-run`
- 執行 `nokori health`，確認 `hooks.pi` 或 `hooks.omp` 顯示 `ok registered`
- 開一個新的 session。recall 會在 `before_agent_start` 注入，Gate 會在 `tool_call` 檢查，關會話後則由 `session_shutdown` 依 runtime session manager 提供的目前 session 檔案啟動提取。
- Pi 的 `/reload` 生命週期會被 bridge 忽略，不會誤判目前會話已結束或提前提取。
- 若設定了 `PI_CODING_AGENT_DIR`，`nokori install --pi` 與 transcript 驗證會使用該目錄，而不是預設的 `~/.pi/agent`。

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
# uv tool
uv tool upgrade nokori

# pipx
pipx upgrade nokori

# pip（venv 內）
pip install --upgrade nokori

# 從原始碼
git pull && pip install -e ".[local-embed,dev]"
```

升級後跑一下 `nokori health` 確認一切正常。Claude Code 與 Cursor 的 Hook 註冊跨版本穩定；若 `hooks.pi` 或 `hooks.omp` 提示產生的橋接已過期，分別執行 `nokori install --pi` 或 `nokori install --omp` 重新整理即可。
