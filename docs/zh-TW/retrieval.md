# 檢索引擎

[← 返回主文件](../../README.zh-TW.md)

---

如何從全部規則中選出與當前提示相關的幾條？三步：BM25 關鍵詞打分，規則足夠多時疊加語義向量（embedding），再用 RRF 融合兩份排名。最後按 HOT / WARM 檔位決定寫入上下文的文字量。

---

## BM25（預設，零依賴）

開箱即用，不需要任何模型或 GPU。

- 索引欄位：`trigger_text`、`trigger_variants`、`search_terms`、`action`
- 拉丁文：轉小寫、切詞，長度 ≥ 2 才收
- CJK：以 bigram（相鄰兩字）為主，落單的單字保留 unigram 以提高召回
- 中英混排自動處理

---

## Embedding（嵌入向量，可選）

規則攢到 **≥ 20 條**、且配了遠端 API 或裝了 `pip install nokori[local-embed]`，語義檢索就自動疊上來。想強制試也行，`NOKORI_EMBED_ENABLED=1`。

兩個都叫「20」的閾值：

| 場景 | 數的是哪批 | 決定什麼 |
|------|-----------|----------|
| **SessionStart** 的 embed kickstart | 全庫 `active + trusted` 總數 | 要不要後台拉起 embed server |
| **UserPromptSubmit** 檢索 | 當次 `formal ∪ shadow` 池大小 | 這條 prompt 走不走 embedding RRF |

### 遠端 API 模式

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
```

### 本地模型模式

```bash
pip install nokori[local-embed]
```

安裝時會裝上 **sentence-transformers>=3.0**。預取模型為 [ibm-granite/granite-embedding-97m-multilingual-r2](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)（97M 參數 / 384 維，約 220MB）。

| 組成部分 | 體積（約） |
|----------|------------|
| `model.safetensors` | ~186 MiB |
| `tokenizer.json` 及 config | ~24 MiB |
| **合計** | ~210–220MB |

權重下載時機：

| 時機 | 說明 |
|------|------|
| `pip install …[local-embed]` | 裝套件後自動 prefetch |
| `nokori install` | 已裝 `[local-embed]` 就 prefetch |
| `nokori embed prefetch` | 手動下載或失敗重試 |

### Hook 內 embed server 行為

- **SessionStart**：本地權重已快取就非阻塞 spawn embed server
- **UserPromptSubmit**：server 還沒 ping 通就後台 spawn，當輪先純 BM25
- Hook 不會等待模型下載或載入

優先順序：遠端 API > 本地 embed server > 純 BM25。

### 本地 embed 管理（Unix）

```bash
nokori embed prefetch   # 下載權重
nokori embed start      # 後台拉起 server
nokori embed status     # 查看狀態
nokori embed stop       # 優雅關閉
```

**平台**：本地 embed 只在 macOS / Linux 上跑（Unix socket）。Windows 走遠端 API 或純 BM25。

---

## 注入分層

檢索完按分數切三檔：

| 層級 | 進檔條件 | 注入內容 |
|------|---------|----------|
| HOT | 通過 runtime applicability 的 `active`/`trusted` 結果且 utility 為正；通常最多 1 條 | trigger + action + rationale |
| WARM | 通過證據線但 utility/歷史/預算不足以 HOT | trigger + action，一行 |
| COLD | Candidate/suppressed/archived、excluded、trigger 證據不足 | 不注入 |

**Trigger evidence** 必須來自規則的 trigger 結構：strong variant phrase + required concepts，或足夠的動態 IDF trigger 資訊。Action-only、search-term-only、embedding-only、excluded-context、near-miss 都留在 COLD。

注入預算：規則 1500 字元，熱快取 500 字元（相互獨立）。僅實際寫入上下文的規則會記錄 fire event。
