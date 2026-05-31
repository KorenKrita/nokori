# 发布到 PyPI（GitHub Actions）

别人安装：

```bash
pip install nokori
nokori install
```

可选本地 embedding（会安装 `sentence-transformers`，并在装包 / `nokori install` 时自动把模型权重下载到 `~/.nokori/models/`）：

```bash
pip install "nokori[local-embed]"
nokori install
# 跳过权重下载：nokori install --no-prefetch-embed
# 手动补下：nokori embed prefetch
```

## 1. 把 API Token 放进 GitHub Secrets（不要写进代码）

1. 打开仓库 **Settings → Secrets and variables → Actions**
2. **New repository secret**
3. Name：`PYPI_API_TOKEN`（必须和 workflow 里一致）
4. Value：粘贴 PyPI 上生成的 token（`pypi-` 开头那一整段）

Token 只存在 GitHub 服务器上；workflow 里用 `${{ secrets.PYPI_API_TOKEN }}`，日志会自动打码，不会出现在仓库里。

可选加固：

- **Settings → Environments** 新建 `pypi`，把 secret 绑在 environment 上，并勾选 “Required reviewers” 发版前要人工点批准
- 若改用 PyPI **Trusted Publishing**（见下文），可不再存长期 token

## 2. 发一个 GitHub Release 触发上传

1. 在 `pyproject.toml` 里改好 `version`（例如 `0.1.1`），提交并推到 `main`
2. GitHub → **Releases → Draft a new release**
3. Tag：`v0.1.1`（建议和版本一致，带不带 `v` 均可，workflow 会去掉 `v` 再比对）
4. 发布 Release（**Publish release**）

会跑 [`.github/workflows/publish-pypi.yml`](workflows/publish-pypi.yml)：

- 校验 tag 与 `pyproject.toml` 的 `version` 一致
- 跑 `pytest`
- `python -m build` 打 wheel / sdist
- 用 secret 上传到 PyPI

也可在 **Actions → Publish to PyPI → Run workflow** 手动触发（不校验 tag；适合修 workflow，正式发版仍建议走 Release）。

## 3. 首次发版检查清单

- [ ] PyPI 上项目名 `nokori` 未被占用（或已是你自己的）
- [ ] `PYPI_API_TOKEN` 已写入 GitHub Secrets
- [ ] `pyproject.toml` 的 `version` 与 Release tag 一致（`v0.1.0` ↔ `0.1.0`）
- [ ] Release 发布后 Actions 里 job 为绿色

## 4.（推荐）Trusted Publishing：不用在 GitHub 存 API Key

PyPI 支持用 **OIDC** 信任 GitHub Actions，长期 token 可以不放 GitHub。

1. [pypi.org](https://pypi.org) → Account → **Publishing** → **Add a new pending publisher**
2. Owner：`KorenKrita`，Repository：`nokori`，Workflow：`publish-pypi.yml`，Environment 留空（除非你在 workflow 里加了 `environment:`）
3. 把 workflow 里 Publish 一步改成**不写** `password:`，并给 job 加权限：

```yaml
permissions:
  id-token: write
  contents: read

# ...
- uses: pypa/gh-action-pypi-publish@release/v1
```

4. 在 PyPI 上 **删除或轮换** 旧 API token

当前仓库默认仍用 `PYPI_API_TOKEN` secret，配置完 Trusted Publishing 后再改 workflow 即可。

## 5. 测试站（可选）

先在 [test.pypi.org](https://test.pypi.org) 建 token / publisher，secret 名例如 `TEST_PYPI_API_TOKEN`，复制 workflow 增加 `repository-url: https://test.pypi.org/legacy/` 的 job，确认后再发正式 PyPI。
