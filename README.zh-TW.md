# Nokori 残り

<p align="center">
  <img src="docs/assets/logo.png" width="160" height="160" alt="Nokori" />
</p>

<p align="center">
  <strong>本地優先的記憶層，把糾正沉澱成持久的 Agent 行為。</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/v/nokori?style=flat-square&color=111827" alt="PyPI" /></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python >= 3.11" />
  <a href="https://github.com/KorenKrita/nokori/blob/main/LICENSE"><img src="https://img.shields.io/github/license/KorenKrita/nokori?style=flat-square&color=0f766e" alt="License" /></a>
  <a href="https://github.com/KorenKrita/nokori/stargazers"><img src="https://img.shields.io/github/stars/KorenKrita/nokori?style=flat-square&color=f59e0b" alt="Stars" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Claude%20Code-ready-4f46e5?style=flat-square" alt="Claude Code ready" />
  <img src="https://img.shields.io/badge/Cursor-ready-2563eb?style=flat-square" alt="Cursor ready" />
  <img src="https://img.shields.io/badge/local--first-SQLite-0f766e?style=flat-square&logo=sqlite&logoColor=white" alt="Local-first SQLite" />
  <img src="https://img.shields.io/badge/retrieval-BM25%20%2B%20embeddings-7c3aed?style=flat-square" alt="BM25 plus embeddings" />
  <img src="https://img.shields.io/badge/Gate-risk%20blocker-dc2626?style=flat-square" alt="Gate risk blocker" />
  <img src="https://img.shields.io/badge/pipx-ready-9333ea?style=flat-square" alt="pipx ready" />
  <img src="https://img.shields.io/badge/offline-optional-0891b2?style=flat-square" alt="Offline optional" />
  <img src="https://img.shields.io/badge/status-alpha-f97316?style=flat-square" alt="Alpha status" />
</p>

<p align="center">
  <sub>記住糾正 · 在上下文中召回規則 · 攔截危險工具呼叫 · 資料全程本地保存</sub>
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

<table>
  <tr>
    <td width="33%">
      <strong>反覆踩坑的人</strong><br />
      強推、忘跑遷移、對著錯誤的庫敲命令：Nokori 會在會話結束後記住這次糾正。
    </td>
    <td width="33%">
      <strong>跨專案偏好維護者</strong><br />
      一次教會一條行為規則，讓它跟著你跨專案流動，而不是每開一個 repo 就重建一套提示。
    </td>
    <td width="33%">
      <strong>本地優先使用者</strong><br />
      規則儲存在本機 SQLite，隨時匯出；檢索時整段聊天不會外傳。
    </td>
  </tr>
</table>

## Before / After

| 沒有 Nokori | 有了 Nokori |
|-------------|-------------|
| 每個 session 都要重複同一條糾正 | 糾正會變成持久的行為規則 |
| 危險工具呼叫依賴 Agent 自己記得上下文 | trusted Gate 規則能在工具執行前攔一下 |
| 偏好隨著聊天視窗一起消失 | 規則留在本地，並跟隨你跨專案使用 |
| 檢索意味著等待模型 | 熱路徑召回只做確定性檔案 I/O 與打分 |

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

四條命令。本地記憶。沒有託管資料庫。

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
  --severity high_risk

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

<table>
  <tr>
    <td width="50%">
      <strong>自治品質飛輪</strong><br />
      candidate → active → trusted，規則必須累積足夠證據才能取得更高權限。
    </td>
    <td width="50%">
      <strong>熱路徑零模型呼叫</strong><br />
      Hook 只做確定性檢索、匹配與打分；prompt 和回覆之間無 LLM 等待。
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>混合檢索</strong><br />
      BM25 開箱即用，可選本地或遠端語義向量；兩者同時可用時用 RRF 融合。
    </td>
    <td width="50%">
      <strong>保守 Gate</strong><br />
      只有 trusted + gate_eligible 規則可以攔工具，而且每輪只攔一次。
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>影子證據</strong><br />
      Candidate 在後台累積反事實證據，不干擾當前對話。
    </td>
    <td width="50%">
      <strong>本地優先儲存</strong><br />
      SQLite + 檔案系統；召回時資料不出本機，也可選擇離線 LLM。
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>跨工具支援</strong><br />
      原生支援 Claude Code 與 Cursor。
    </td>
    <td width="50%">
      <strong>Web UI</strong><br />
      執行 <code>nokori web</code>，用視覺化面板查看規則、日誌、生命週期狀態與設定。
    </td>
  </tr>
</table>

---

## 文件

| 指南 | 能幫你了解什麼 |
|------|------------------|
| 🚀 [安裝指南](docs/zh-TW/installation.md) | pipx 安裝、Cursor 設定、更新與解除安裝 |
| 🧠 [架構詳解](docs/zh-TW/architecture.md) | 飛輪機制、Hook 時序、注入 vs Gate、Shadow Pool |
| ⚙️ [設定](docs/zh-TW/configuration.md) | `config.toml`、環境變數、完整參考 |
| 🔎 [檢索引擎](docs/zh-TW/retrieval.md) | BM25、Embedding、RRF 融合、注入分層 |
| 🌱 [規則生命週期](docs/zh-TW/lifecycle.md) | 狀態機、晉升證據、維護任務 |
| 🧊 [自動提取](docs/zh-TW/extraction.md) | 冷路徑 pipeline、Merge 策略、Async 模式 |
| 🛡️ [Gate 機制](docs/zh-TW/gate.md) | 兩層匹配、設定、Prompt-hash 安全 |
| ⌨️ [CLI 參考](docs/zh-TW/cli.md) | 全部指令與選項 |
| 🖥️ [Web UI](docs/zh-TW/web-ui.md) | 視覺化面板功能與開發 |

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

專案約束：熱路徑 hook 僅使用 stdlib + urllib（prompt 到回覆之間無 LLM 呼叫），所有 hook 頂層 try/except fail-open。基礎安裝包含 fastapi + uvicorn 用於 Web 儀表盤。

---

## License

MIT
