# Contributing to Nokori

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,local-embed]"
pre-commit install
```

`local-embed` pulls sentence-transformers for optional local semantic retrieval. Dev extras include pytest, ruff, mypy, and pre-commit.

## Verify

```bash
python scripts/verify.py   # ruff + mypy ratchet + pytest
```

Optional coverage on hot-path modules (non-blocking in CI):

```bash
bash scripts/coverage.sh
```

## Web UI

Source lives in `web/`. Built assets are committed under `nokori/web/static/` so the Python package serves a dashboard without a separate Node build at runtime.

```bash
cd web
npm ci
npm run lint
npm test
npm run build   # writes to ../nokori/web/static — commit the result
```

## Docs / locales

Keep README locale siblings (`README.md`, `README.zh-CN.md`, `README.zh-TW.md`, `README.ja.md`) in parity for development and install sections when those change. UI strings in `web/src/lib/i18n.ts` must share the same key set across `zh` / `en` / `ja` (enforced by Vitest).

## Releases

GitHub release tags must match the package version in `pyproject.toml` (e.g. tag `v0.5.54` for version `0.5.54`). The publish workflow expects that alignment.
