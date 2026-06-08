# Web UI 可视化面板

[← 返回主文档](../../README.zh-CN.md)

---

Nokori 内置本地可视化管理面板，一条命令查看所有运行状态。

```bash
nokori web                    # 自动打开 http://localhost:8765
nokori web --port 9000        # 自定义端口
nokori web --no-browser       # 仅启动服务器
```

---

## 页面一览

| 页面 | 内容 |
|------|------|
| **仪表盘** | 规则各状态计数、24h 注入统计、Embed 服务控制、Gate 状态、待处理提取任务、生命周期证据 |
| **规则** | 筛选列表、详情页（trigger、action、evidence log、lifecycle evidence、replacement lineage）、编辑、退役 |
| **检索模拟** | 输入 prompt 查看命中规则：BM25 + embedding 分数、HOT/WARM 分层、匹配 token、影子池 |
| **活动 — 时间线** | 全系统事件流：hook 调用、冷管道决策、生命周期迁移、事后评估。彩色类型标签、结果徽章、session/类型筛选 |
| **活动 — Dashboard** | 运营图表：事件来源柱状图、冷管道转化漏斗、错误饼图、错误趋势折线图 |
| **注入历史** | 每次规则注入的时间线，可按级别/会话筛选 |
| **提取管道** | 待处理/已完成任务、每个转录文件的提取状态 |
| **生命周期** | candidate → active、active → trusted、suppressed recovery 的证据进度 |
| **配置与健康** | 当前配置 + 各项健康检查 |
| **日志** | WebSocket 实时日志流，支持级别筛选 |

---

## 特性

- **多语言**：自动检测浏览器语言，支持中文/英文/日文切换
- **深色/浅色模式**：默认跟随系统 `prefers-color-scheme`，可手动切换
- **Embed 服务控制**：在面板上直接启动/停止本地 embedding 服务
- **精致动效**：数字跳动、光标跟随光晕、浮动渐变背景、交错入场动画

---

## 前端开发

```bash
cd web
npm install
npm run dev          # Vite 开发服务器 :5173，代理 /api 到 :8765
# 另一个终端：
nokori web --no-browser   # 启动 API 后端
```
