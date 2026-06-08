# Installation Guide

[← Back to main README](../../README.md)

---

## Before you begin

- **Python ≥ 3.11** (hot-path hooks use only stdlib; base install includes fastapi + uvicorn + websockets for the web dashboard)
- **Claude Code** or **Cursor** already installed (either one)
- For local semantic retrieval, leave about **220MB** of disk for the embedding model weights (optional)

Three ways to install, pick one: local model (recommended), minimal install, or from source.

---

## macOS / Linux: do not `pip install` into system Python

Python from Homebrew and many Linux distros is [PEP 668](https://peps.python.org/pep-0668/) **externally managed**. A bare `pip install nokori` fails with **`externally-managed-environment`**. Use **pipx** (recommended) or a **dedicated venv** — not `--break-system-packages`.

### Option A: `pipx` (recommended for CLI use)

```bash
brew install pipx
pipx ensurepath
# open a new terminal, or source ~/.zshrc

pipx install "nokori[local-embed]"
nokori install --all        # or --cursor / Claude-only default
nokori health
```

`pipx` installs into an isolated app venv; the `nokori` command is usually `~/.local/bin/nokori`. `nokori install` registers hooks as that environment's `python -I -m nokori hook`.

### Option B: dedicated venv

```bash
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install -U pip
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

nokori install --all
nokori health
```

---

## From PyPI (recommended: local semantic retrieval)

This path runs semantic retrieval on your own machine, no embedding API key required. It installs **sentence-transformers** and, on `nokori install`, prefetches the local embedding model **[IBM Granite Embedding 97M](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)** (`ibm-granite/granite-embedding-97m-multilingual-r2`) into `~/.nokori/models/`: **97M params / 384-dim**, ~**220MB** download.

After installing via **pipx** or **venv** above:

```bash
# Register hooks
nokori install              # Claude Code  → ~/.claude/settings.json
nokori install --cursor     # native Cursor only → ~/.cursor/hooks.json
nokori install --all        # Claude + Cursor

# Verify
nokori health
nokori status
nokori logs                 # hook / pipeline / async-extract logs
```

Common side branches:

- **Skip weight download**: `nokori install --no-prefetch-embed`
- **Download manually / retry**: `nokori embed prefetch`
- **Debug hooks**: set `log_level = "info"` in `config.toml`, or `export NOKORI_LOG_LEVEL=info`

---

## Minimal install (no local model)

```bash
pipx install nokori
nokori install
```

BM25 keyword retrieval works out of the box and is plenty. When you want semantic retrieval, two paths: point at any OpenAI-compatible embedding API (set `NOKORI_EMBED_BASE_URL`, `NOKORI_EMBED_MODEL`), or add `pip install "nokori[local-embed]"` later.

---

## Development (from source)

```bash
git clone https://github.com/KorenKrita/nokori.git
cd nokori
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[local-embed,dev]"

nokori install
```

`nokori install` **merges** hooks into `~/.claude/settings.json`, never touching other plugins you already have.

```bash
# Preview what would be written, no disk changes
nokori install --dry-run

# Uninstall (removes only nokori hooks)
nokori install --uninstall

# Temporarily disable
nokori install --disable
nokori install --enable
```

---

## Claude Code and Cursor

**Claude Code** by default; **Cursor** is supported too (native hooks or import from Claude). On one machine pick a single Cursor registration path.

### Which install command?

| Goal | Command |
|------|---------|
| Claude Code only | `nokori install` |
| Cursor only (native `~/.cursor/hooks.json`) | `nokori install --cursor` |
| Both platforms | `nokori install --all` |

### Pick exactly one Cursor path (do not mix)

| Path | What you do | Good when |
|------|-------------|-----------|
| **A — Import from Claude** | `nokori install`, then in Cursor: Settings → Hooks → Import from Claude Code | You already use Claude Code |
| **B — Native Cursor** | run `nokori install --cursor` only; do not also enable Claude import | Cursor-only |

**If both paths are live**, the same user message can trigger Nokori twice. **Hook coalesce** is on by default (`NOKORI_HOOK_COALESCE=1`): only the first invocation runs retrieve/Gate/extract, the second passes through empty. `nokori health` warns when both are registered.

### Cursor-only things to watch

- **Terminal tool name**: Cursor uses `Shell`, Claude Code uses `Bash`. `nokori install --cursor` includes `Shell` in the preToolUse matcher.
- **Deferred inject**: for a turn where Cursor never fired `beforeSubmitPrompt`, the first matching `preToolUse` may deny once and carry the rule text. Run the same tool again after the deny.

---

## Updating

```bash
# pipx
pipx upgrade nokori

# pip (inside venv)
pip install --upgrade nokori

# from source
git pull && pip install -e ".[local-embed,dev]"
```

After upgrading, run `nokori health` to confirm everything still checks out. Hook registrations are stable across upgrades (no need to re-run `nokori install`).
