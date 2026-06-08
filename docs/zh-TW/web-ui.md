# Web UI 視覺化面板

[← 返回主文件](../../README.zh-TW.md)

---

Nokori 內建本地視覺化管理面板，一條指令查看所有執行狀態。

```bash
nokori web                    # 自動開啟 http://localhost:8765
nokori web --port 9000        # 自訂連接埠
nokori web --no-browser       # 僅啟動伺服器
```

---

## 頁面一覽

| 頁面 | 內容 |
|------|------|
| **儀表板** | 規則各狀態計數、24h 注入統計、Embed 服務控制、Gate 狀態、待處理提取任務、生命週期證據 |
| **規則** | 篩選列表、詳情頁（trigger、action、evidence log、lifecycle evidence、replacement lineage）、編輯、退役 |
| **檢索模擬** | 輸入 prompt 查看命中規則：BM25 + embedding 分數、HOT/WARM 分層、匹配 token、影子池 |
| **活動 — 時間線** | 全系統事件流：hook 呼叫、冷管道決策、生命週期遷移、事後評估。彩色類型標籤、結果徽章、session/類型篩選 |
| **活動 — Dashboard** | 營運圖表：事件來源柱狀圖、冷管道轉化漏斗、錯誤圓餅圖、錯誤趨勢折線圖 |
| **注入歷史** | 每次規則注入的時間線，可按層級/會話篩選 |
| **提取管道** | 待處理/已完成任務、每個轉錄檔案的提取狀態 |
| **生命週期** | candidate → active、active → trusted、suppressed recovery 的證據進度 |
| **設定與健康** | 當前設定 + 各項健康檢查 |
| **日誌** | WebSocket 即時日誌串流，支援層級篩選 |

---

## 特性

- **多語言**：自動偵測瀏覽器語言，支援中文/英文/日文切換
- **深色/淺色模式**：預設跟隨系統 `prefers-color-scheme`，可手動切換
- **Embed 服務控制**：在面板上直接啟動/停止本地 embedding 服務
- **精緻動效**：數字跳動、游標跟隨光暈、浮動漸層背景、交錯入場動畫

---

## 前端開發

```bash
cd web
npm install
npm run dev          # Vite 開發伺服器 :5173，代理 /api 到 :8765
# 另一個終端：
nokori web --no-browser   # 啟動 API 後端
```
