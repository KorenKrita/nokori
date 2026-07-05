# 安装指南

[← 返回主文档](../../README.zh-CN.md)

---

## 开始之前

- **Python ≥ 3.11**（热路径 hook 仅使用 stdlib；基础安装包含 fastapi + uvicorn + websockets 用于 Web 仪表盘）
- 已装好 **Claude Code**、**Cursor** 或 **OMP** 任意一个
- 想用本地语义检索，预留约 **220MB** 磁盘装嵌入模型权重（可选）

三种装法，按需挑一种：本地模型（推荐）、最小安装、从源码开发。

---

## macOS / Linux：别用系统 `pip` 直装

Homebrew 等自带的 Python 受 [PEP 668](https://peps.python.org/pep-0668/) 保护，直接 `pip install nokori` 会报 **`externally-managed-environment`**。请用 **pipx**（推荐）或 **专用 venv**，不要用 `--break-system-packages`。

### 方式 A：`pipx`（推荐，适合 CLI）

```bash
brew install pipx
pipx ensurepath
# 新开一个终端，或 source ~/.zshrc

pipx install "nokori[local-embed]"
nokori install --omp        # 仅 OMP；--all 仍只装 Claude Code + Cursor
nokori health
```

`pipx` 把 `nokori` 装进独立环境，命令一般在 `~/.local/bin/nokori`；Claude Code / Cursor 的 hook 仍会调用该环境里的 `python -I -m nokori hook`，`--omp` 则额外写入一个 TypeScript bridge，把 OMP runtime 事件转给同一套 Python dispatcher。

### 方式 B：专用 venv

```bash
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install -U pip
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

nokori install --omp
nokori health
```

---

## 从 PyPI 安装（推荐：本地语义检索）

这条路在本机跑语义检索，不需要任何 embedding API key。它会装上 **sentence-transformers**，并在 `nokori install` 时从 Hugging Face 预取本地嵌入模型 **[IBM Granite Embedding 97M](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)**（`ibm-granite/granite-embedding-97m-multilingual-r2`）到 `~/.nokori/models/`：**97M 参数 / 384 维**，下载约 **220MB**。

按上一节用 **pipx** 或 **venv** 安装后：

```bash
# 注册 hooks / bridge
nokori install              # Claude Code  → ~/.claude/settings.json
nokori install --cursor     # 仅原生 Cursor → ~/.cursor/hooks.json
nokori install --omp        # 仅 OMP         → ~/.omp/agent/extensions/nokori.ts
nokori install --all        # Claude + Cursor

# 验证
nokori health
nokori status
ls ~/.omp/agent/extensions/nokori.ts   # 仅 OMP

几个常用旁支：

- **跳过权重下载**：`nokori install --no-prefetch-embed`
- **手动补下 / 重试**：`nokori embed prefetch`
- **调试 hook**：`config.toml` 里设 `log_level = "info"`，或 `export NOKORI_LOG_LEVEL=info`

---

## 最小安装（不要本地模型）

```bash
pipx install nokori
nokori install
```

开箱就有 BM25 关键词检索，够用。想要语义检索时，接任意 OpenAI 兼容的 embedding API（设 `NOKORI_EMBED_BASE_URL`、`NOKORI_EMBED_MODEL`），或者哪天再补 `pip install "nokori[local-embed]"`。

---

## 从源码开发

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[local-embed,dev]"

nokori install
```

`nokori install` 把 hook **合并**进 `~/.claude/settings.json`，不碰你已经装好的其它插件。

```bash
# 预览将要写入的变更，不落盘
nokori install --dry-run

# 卸载（只摘掉 nokori 的 hooks）
nokori install --uninstall

# 临时停用
nokori install --disable
nokori install --enable
```

---

## Claude Code、Cursor 与 OMP

默认装 **Claude Code**；也支持 **Cursor** 与 **OMP**。OMP 会在 `~/.omp/agent/extensions/nokori.ts` 安装一个小型 TypeScript bridge，把 runtime 事件转给 Nokori 现有的 Python dispatcher。

### 装哪条命令？

OMP 需要显式安装：`--all` 仍只代表 Claude Code + Cursor。

| 目标 | 命令 | 写入位置 |
|------|------|----------|
| 仅 Claude Code | `nokori install` | `~/.claude/settings.json` |
| 仅 Cursor（原生 `~/.cursor/hooks.json`） | `nokori install --cursor` | `~/.cursor/hooks.json` |
| 仅 OMP | `nokori install --omp` | `~/.omp/agent/extensions/nokori.ts` |
| Claude Code + Cursor | `nokori install --all` | 上面两个文件 |

### 验证 OMP 安装

- 想先看写入内容：`nokori install --omp --dry-run`
- 安装后确认文件存在：`ls ~/.omp/agent/extensions/nokori.ts`
- 新开一场 OMP session；recall 注入走 `before_agent_start`，Gate 检查走 `tool_call`，会话结束后的提取从 `session_shutdown` 开始。

### Cursor 只选一条路（不要混用）

| 路径 | 怎么做 | 适合 |
|------|--------|------|
| **A — 从 Claude 导入** | `nokori install`，再在 Cursor：Settings → Hooks → 从 Claude Code 导入 | 本来就用 Claude Code |
| **B — Cursor 原生** | 只跑 `nokori install --cursor`；不要再开 Claude 导入 | 只要 Cursor |

**若两套都生效**，同一条用户消息可能触发 Nokori 两次。默认开启 **hook 合并**（`NOKORI_HOOK_COALESCE=1`）：只有第一次调用会跑检索/Gate/提取，第二次空跑通过。`nokori health` 会在双注册时警告。

### 仅 Cursor 要注意的

- **终端工具名**：Cursor 用 `Shell`，Claude Code 用 `Bash`。`nokori install --cursor` 会在 preToolUse matcher 里带上 `Shell`。
- **Deferred 注入**：某轮若 Cursor 没触发 `beforeSubmitPrompt`，第一次匹配的 `preToolUse` 可能 deny 一次带上规则。deny 后请再执行同一工具一次。
---

## 更新

```bash
# pipx
pipx upgrade nokori

# pip（venv 内）
pip install --upgrade nokori

# 从源码
git pull && pip install -e ".[local-embed,dev]"
```

升级后跑一下 `nokori health` 确认一切正常。Hook 注册跨版本稳定，升级后不需要重新 `nokori install`。
