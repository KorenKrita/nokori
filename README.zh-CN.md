# Nokori 残り

<p align="center">
  <img src="docs/assets/logo.png" width="160" height="160" alt="Nokori" />
</p>

<p align="center">
  <strong>为 Claude Code 与 Cursor 锻造的行为记忆层。</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/v/nokori" alt="PyPI" /></a>
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/pyversions/nokori" alt="Python" /></a>
  <a href="https://github.com/KorenKrita/nokori/blob/main/LICENSE"><img src="https://img.shields.io/github/license/KorenKrita/nokori" alt="License" /></a>
  <a href="https://github.com/KorenKrita/nokori/stargazers"><img src="https://img.shields.io/github/stars/KorenKrita/nokori" alt="Stars" /></a>
</p>

<p align="center">
  <b>Languages:</b> <a href="README.md">English</a> | <b>简体中文</b> | <a href="README.zh-TW.md">繁體中文</a> | <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="#快速安装">快速安装</a> · <a href="#一分钟看懂">工作原理</a> · <a href="docs/zh-CN/architecture.md">架构详解</a> · <a href="docs/zh-CN/configuration.md">配置</a> · <a href="docs/zh-CN/cli.md">CLI 参考</a> · <a href="docs/zh-CN/web-ui.md">Web UI</a>
</p>

---

> 经验留下的痕迹，比记忆更深。

残り（nokori），意为残留之物：喧嚣散场之后，仍旧留在原地的东西。

每一次对话结束，你纠正过的话都随之蒸发。下一个 session 里，Agent 重新变回那个会强推、会忘跑迁移、会对着生产库敲下危险命令的陌生人。

Nokori 偏不让它忘。它把你说过的「别这么干」沉淀成可召回的行为规则：当你的话再次逼近那个场景，规则自动浮现在 Agent 的上下文里。新规则先作为候选沉在水下，经冷路径和事后证据确认可靠后，最锋利的那几条才会获得 Gate 资格，在 Agent 碰文件之前拦下第一次危险工具调用。

数据全程留在你机器上的 SQLite 里。聊天时的检索不碰任何模型。只有关会话后的提取才动用 LLM，喂给它的也只是压缩过的会话片段；想彻底离线，端点指向本地 Ollama 就行。

---

## 它适合谁

- 反复纠正同一类问题的人：强推、忘跑迁移、对着错误的库敲命令
- 想要**跨项目**沉淀一套「别这么干」的人，而不是每开一个 repo 就从头教一遍
- 信任本地的人：规则存储在本机 SQLite，随时导出，整段聊天不外传

---

## 一分钟看懂

```
你纠正 Claude / Cursor
    └─▶ Nokori 刻下一条规矩（什么场景 + 该怎么做）
            └─▶ 下次你的话又靠近那个场景
                    └─▶ 规矩自动写进 Agent 的上下文（提醒）
                            └─▶ 若它后来变成 trusted + gate_eligible：
                                 第一次匹配的工具调用前，先拦一道（Gate）
```

聊天时 Nokori 只做检索和读写小文件，不会阻塞等待模型。LLM 仅在关会话后用于从 transcript（会话记录）提取新规则。

---

## 快速安装

**前置条件**：Python ≥ 3.11、已安装 Claude Code 或 Cursor

```bash
# 推荐：pipx 安装（含本地语义检索）
brew install pipx && pipx ensurepath
pipx install "nokori[local-embed]"

# 注册 hooks
nokori install --all        # 或 --cursor / 默认只装 Claude Code

# 验证
nokori health
```

<details>
<summary>其它安装方式</summary>

```bash
# 最小安装（仅 BM25 检索，不含本地模型）
pipx install nokori

# 专用 venv
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc

# 从源码
git clone https://github.com/KorenKrita/nokori.git && cd nokori
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
```

</details>

> 详细安装指南（Cursor 配置、更新、卸载等）见 [安装文档](docs/zh-CN/installation.md)

---

## 快速开始

```bash
# 1. 添加一条候选规则
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --severity high_risk

# 2. 验证影子命中
nokori test "I'll just git push --force this branch"

# 3. 运行维护（让证据推动规则进入正式池）
nokori maintain

# 4. 规则过时了？退役它
nokori dismiss <short_id>
```

照常开 Claude Code / Cursor 写代码就行——匹配到规则时，Agent 回复前就能看到注入的提醒；对 `trusted` + `gate_eligible` 的规则，第一次敏感工具调用会被拦一下。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **自治质量飞轮** | candidate → active → trusted，规则必须攒够证据才能变强势 |
| **热路径零模型调用** | Hook 只做确定性检索/匹配/打分，prompt 和回复之间无 LLM 等待 |
| **混合检索** | BM25 开箱即用 + 可选本地/远程语义向量，RRF 融合 |
| **保守 Gate** | 仅 trusted + gate_eligible 规则可拦工具，且只拦一次 |
| **影子证据** | Candidate 在后台积累反事实证据，不干扰当前对话 |
| **本地优先** | SQLite + 文件系统，数据不出本机，可选离线 LLM |
| **跨工具支持** | Claude Code 与 Cursor 原生支持 |
| **Web UI** | 一条命令 `nokori web` 可视化管理所有状态 |

---

## 文档

| 文档 | 内容 |
|------|------|
| [架构详解](docs/zh-CN/architecture.md) | 飞轮机制、Hook 时序、注入 vs Gate、Shadow Pool |
| [安装指南](docs/zh-CN/installation.md) | 各平台安装、Cursor 配置、更新与卸载 |
| [配置](docs/zh-CN/configuration.md) | config.toml、环境变量完整参考 |
| [检索引擎](docs/zh-CN/retrieval.md) | BM25、Embedding、注入分层 |
| [规则生命周期](docs/zh-CN/lifecycle.md) | 状态机、晋升条件、维护任务 |
| [自动提取](docs/zh-CN/extraction.md) | 冷路径 pipeline、Merge 策略、Async 模式 |
| [Gate 机制](docs/zh-CN/gate.md) | 两层匹配、配置、Prompt-hash 安全 |
| [CLI 参考](docs/zh-CN/cli.md) | 全部命令与选项 |
| [Web UI](docs/zh-CN/web-ui.md) | 可视化面板功能与开发 |

---

## 与现有系统的关系

| 系统 | 关系 |
|------|------|
| CLAUDE.md | 互补。Nokori 不碰你的 CLAUDE.md；它管的是动态的「遇到 X 就做 Y」 |
| Claude Code auto-memory | 不冲突。memory 偏记事实，Nokori 偏记行为规矩 |
| 其他 memory 插件 | hook 可共存，但避免叠加过多会向上下文注入内容的插件 |

---

## 数据存储

所有数据在本地 `~/.nokori/` 一个目录。没有网络同步，规则里存的是行为描述，不含源代码。只有冷路径提取会调 LLM，端点指向本地 Ollama 就能彻底离线。

---

## 开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
python -m pytest tests/
```

项目约束：热路径 hook 仅使用 stdlib + urllib（prompt 到回复之间无 LLM 调用），所有 hook 顶层 try/except fail-open。基础安装包含 fastapi + uvicorn 用于 Web 仪表盘。

---

## License

MIT
