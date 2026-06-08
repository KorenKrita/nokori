# Nokori 残り

<p align="center">
  <img src="docs/assets/logo.png" width="160" height="160" alt="Nokori" />
</p>

<p align="center">
  <strong>為 Claude Code 與 Cursor 鍛造的行為記憶層。</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/v/nokori" alt="PyPI" /></a>
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/pyversions/nokori" alt="Python" /></a>
  <a href="https://github.com/KorenKrita/nokori/blob/main/LICENSE"><img src="https://img.shields.io/github/license/KorenKrita/nokori" alt="License" /></a>
  <a href="https://github.com/KorenKrita/nokori/stargazers"><img src="https://img.shields.io/github/stars/KorenKrita/nokori" alt="Stars" /></a>
</p>

<p align="center">
  <b>Languages:</b> <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a> | <b>繁體中文</b> | <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="#快速安裝">快速安裝</a> · <a href="#一分鐘看懂">工作原理</a> · <a href="docs/zh-TW/architecture.md">架構詳解</a> · <a href="docs/zh-TW/configuration.md">設定</a> · <a href="docs/zh-TW/cli.md">CLI 參考</a> · <a href="docs/zh-TW/web-ui.md">Web UI</a>
</p>

---

> 經驗留下的痕跡，比記憶更深。

残り（nokori），意為殘留之物：喧囂散場之後，仍舊留在原地的東西。

每一次對話結束，你糾正過的話都隨之蒸發。下一個 session 裡，Agent 重新變回那個會強推、會忘跑遷移、會對著生產庫敲下危險命令的陌生人。

Nokori 偏不讓它忘。它把你說過的「別這麼幹」沉澱成可召回的行為規則：當你的話再次逼近那個場景，規則自動浮現在 Agent 的上下文裡。新規則先作為候選沉在水下，經冷路徑和事後證據確認可靠後，最鋒利的那幾條才會取得 Gate 資格，在 Agent 碰檔案之前攔下第一次危險工具呼叫。

資料全程留在你機器上的 SQLite 裡。聊天時的檢索不碰任何模型。只有關會話後的提取才動用 LLM，餵給它的也只是壓縮過的會話片段；想徹底離線，端點指向本地 Ollama 就行。

---

## 它適合誰

- 反覆糾正同一類問題的人：強推、忘跑遷移、對著錯誤的庫敲命令
- 想要**跨專案**沉澱一套「別這麼幹」的人，而不是每開一個 repo 就從頭教一遍
- 信任本地的人：規則儲存在本機 SQLite，隨時匯出，整段聊天不外傳

---

## 一分鐘看懂

```
你糾正 Claude / Cursor
    └─▶ Nokori 刻下一條規矩（什麼場景 + 該怎麼做）
            └─▶ 下次你的話又靠近那個場景
                    └─▶ 規矩自動寫進 Agent 的上下文（提醒）
                            └─▶ 若它後來變成 trusted + gate_eligible：
                                 第一次匹配的工具呼叫前，先攔一道（Gate）
```

聊天時 Nokori 只做檢索和讀寫小檔案，不會阻塞等待模型。LLM 僅在關會話後用於從 transcript（會話記錄）提取新規則。

---

## 快速安裝

**前置條件**：Python ≥ 3.11、已安裝 Claude Code 或 Cursor

```bash
# 推薦：pipx 安裝（含本地語義檢索）
brew install pipx && pipx ensurepath
pipx install "nokori[local-embed]"

# 註冊 hooks
nokori install --all        # 或 --cursor / 預設只裝 Claude Code

# 驗證
nokori health
```

<details>
<summary>其它安裝方式</summary>

```bash
# 最小安裝（僅 BM25 檢索，不含本地模型）
pipx install nokori

# 專用 venv
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc

# 從原始碼
git clone https://github.com/KorenKrita/nokori.git && cd nokori
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
```

</details>

> 詳細安裝指南（Cursor 設定、更新、解除安裝等）見 [安裝文件](docs/zh-TW/installation.md)

---

## 快速開始

```bash
# 1. 新增一條候選規則
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --rationale "force push overwrites peers' work" \
  --source-type correction --confidence high

# 2. 驗證影子命中
nokori test "I'll just git push --force this branch"

# 3. 執行維護（讓證據推動規則進入正式池）
nokori maintain

# 4. 規則過時了？退役它
nokori dismiss <short_id>
```

照常開 Claude Code / Cursor 寫程式就行——匹配到規則時，Agent 回覆前就能看到注入的提醒；對 `trusted` + `gate_eligible` 的規則，第一次敏感工具呼叫會被攔一下。

---

## 核心特性

| 特性 | 說明 |
|------|------|
| **自治品質飛輪** | candidate → active → trusted，規則必須攢夠證據才能變強勢 |
| **熱路徑零模型呼叫** | Hook 只做確定性檢索/匹配/打分，prompt 和回覆之間無 LLM 等待 |
| **混合檢索** | BM25 開箱即用 + 可選本地/遠端語義向量，RRF 融合 |
| **保守 Gate** | 僅 trusted + gate_eligible 規則可攔工具，且只攔一次 |
| **影子證據** | Candidate 在後台累積反事實證據，不干擾當前對話 |
| **本地優先** | SQLite + 檔案系統，資料不出本機，可選離線 LLM |
| **跨工具支援** | Claude Code 與 Cursor 原生支援 |
| **Web UI** | 一條指令 `nokori web` 視覺化管理所有狀態 |

---

## 文件

| 文件 | 內容 |
|------|------|
| [架構詳解](docs/zh-TW/architecture.md) | 飛輪機制、Hook 時序、注入 vs Gate、Shadow Pool |
| [安裝指南](docs/zh-TW/installation.md) | 各平台安裝、Cursor 設定、更新與解除安裝 |
| [設定](docs/zh-TW/configuration.md) | config.toml、環境變數完整參考 |
| [檢索引擎](docs/zh-TW/retrieval.md) | BM25、Embedding、注入分層 |
| [規則生命週期](docs/zh-TW/lifecycle.md) | 狀態機、晉升條件、維護任務 |
| [自動提取](docs/zh-TW/extraction.md) | 冷路徑 pipeline、Merge 策略、Async 模式 |
| [Gate 機制](docs/zh-TW/gate.md) | 兩層匹配、設定、Prompt-hash 安全 |
| [CLI 參考](docs/zh-TW/cli.md) | 全部指令與選項 |
| [Web UI](docs/zh-TW/web-ui.md) | 視覺化面板功能與開發 |

---

## 與現有系統的關係

| 系統 | 關係 |
|------|------|
| CLAUDE.md | 互補。Nokori 不碰你的 CLAUDE.md；它管的是動態的「遇到 X 就做 Y」 |
| Claude Code auto-memory | 不衝突。memory 偏記事實，Nokori 偏記行為規矩 |
| 其他 memory 外掛 | hook 可共存，但避免疊加過多會向上下文注入內容的外掛 |

---

## 資料儲存

所有資料在本地 `~/.nokori/` 一個目錄。沒有網路同步，規則裡存的是行為描述，不含原始碼。只有冷路徑提取會呼叫 LLM，端點指向本地 Ollama 就能徹底離線。

---

## 開發

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
python -m pytest tests/
```

專案約束：核心純 stdlib + urllib，熱路徑禁 LLM 呼叫，所有 hook 頂層 try/except fail-open。

---

## License

MIT
