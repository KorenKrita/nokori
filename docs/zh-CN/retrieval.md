# 检索引擎

[← 返回主文档](../../README.zh-CN.md)

---

如何从全部规则中选出与当前提示相关的几条？三步：BM25 关键词打分，规则足够多时叠加语义向量（embedding），再用 RRF 融合两份排名。最后按 HOT / WARM 档位决定写入上下文的文本量。

---

## BM25（默认，零依赖）

开箱即用，不需要任何模型或 GPU。

- 索引字段：`trigger_text`、`trigger_variants`、`search_terms`、`action`
- 拉丁文：转小写、切词，长度 ≥ 2 才收
- CJK：以 bigram（相邻两字）为主，落单的单字保留 unigram 以提高召回
- 中英混排自动处理

---

## Embedding（嵌入向量，可选）

规则攒到 **≥ 20 条**、且配了远程 API 或装了 `pip install nokori[local-embed]`，语义检索就自动叠上来。想强制试也行，`NOKORI_EMBED_ENABLED=1`。

两个都叫「20」的阈值：

| 场景 | 数的是哪批 | 决定什么 |
|------|-----------|----------|
| **SessionStart** 的 embed kickstart | 全库 `active + trusted` 总数 | 要不要后台拉起 embed server |
| **UserPromptSubmit** 检索 | 当次 `formal ∪ shadow` 池大小 | 这条 prompt 走不走 embedding RRF |

### 远程 API 模式

```bash
export NOKORI_EMBED_BASE_URL="http://localhost:11434/v1"
export NOKORI_EMBED_MODEL="nomic-embed-text"
```

### 本地模型模式

```bash
pip install nokori[local-embed]
```

安装时会装上 **sentence-transformers>=3.0**。预取模型为 [ibm-granite/granite-embedding-97m-multilingual-r2](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)（97M 参数 / 384 维，约 220MB）。

| 组成部分 | 体积（约） |
|----------|------------|
| `model.safetensors` | ~186 MiB |
| `tokenizer.json` 及 config | ~24 MiB |
| **合计** | ~210–220MB |

权重下载时机：

| 时机 | 说明 |
|------|------|
| `pip install …[local-embed]` | 装包后自动 prefetch |
| `nokori install` | 已装 `[local-embed]` 就 prefetch |
| `nokori embed prefetch` | 手动下载或失败重试 |

### Hook 内 embed server 行为

- **SessionStart**：本地权重已缓存就非阻塞 spawn embed server
- **UserPromptSubmit**：server 还没 ping 通就后台 spawn，当轮先纯 BM25
- Hook 不会等待模型下载或加载

优先级：远程 API > 本地 embed server > 纯 BM25。

### 本地 embed 管理（Unix）

```bash
nokori embed prefetch   # 下载权重
nokori embed start      # 后台拉起 server
nokori embed status     # 查看状态
nokori embed stop       # 优雅关闭
```

**平台**：本地 embed 只在 macOS / Linux 上跑（Unix socket）。Windows 走远程 API 或纯 BM25。

---

## 注入分层

检索完按分数切三档：

| 层级 | 进档条件 | 注入内容 |
|------|---------|----------|
| HOT | 通过 runtime applicability 的 `active`/`trusted` 结果且 utility 为正；通常最多 1 条 | trigger + action + rationale |
| WARM | 通过证据线但 utility/历史/预算不足以 HOT | trigger + action，一行 |
| COLD | Candidate/suppressed/archived、excluded、trigger 证据不足 | 不注入 |

**Trigger evidence** 必须来自规则的 trigger 结构：strong variant phrase + required concepts，或足够的动态 IDF trigger 信息。Action-only、search-term-only、embedding-only、excluded-context、near-miss 都留在 COLD。

注入预算：规则 1500 字符，热缓存 500 字符（相互独立）。仅实际写入上下文的规则会记录 fire event。
