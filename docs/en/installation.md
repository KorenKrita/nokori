# Installation Guide

[← Back to main README](../../README.md)

---

## Before you begin

- **Python ≥ 3.11** (hot-path hooks use only stdlib; base install includes fastapi + uvicorn + websockets for the web dashboard)
- **Claude Code**, **Cursor**, **Pi**, or **OMP** already installed
- For local semantic retrieval, leave about **220MB** of disk for the embedding model weights (optional)

Three ways to install, pick one: local model (recommended), minimal install, or from source.

---

## macOS / Linux: do not `pip install` into system Python

Python from Homebrew and many Linux distros is [PEP 668](https://peps.python.org/pep-0668/) **externally managed**. A bare `pip install nokori` fails with **`externally-managed-environment`**. Use **uv tool** (recommended), **pipx**, or a **dedicated venv** — not `--break-system-packages`.

### Option A: `uv tool` (recommended for CLI use)

```bash
# macOS; see https://docs.astral.sh/uv/getting-started/installation/ for other platforms
brew install uv
uv tool install "nokori[local-embed]"

nokori install --pi         # Pi only; use --omp for OMP or --all for Claude Code + Cursor
nokori health
```

`uv tool` creates an isolated environment and exposes the `nokori` command without modifying system Python. Claude Code and Cursor call that environment's `python -I -m nokori hook` directly. `--pi` and `--omp` write TypeScript bridges that forward runtime events into the same Python dispatcher.

### Option B: `pipx`

```bash
brew install pipx
pipx ensurepath
# open a new terminal, or source ~/.zshrc

pipx install "nokori[local-embed]"
```

`pipx` provides the same isolated CLI model and is a supported alternative.

### Option C: dedicated venv

```bash
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install -U pip
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

nokori install --pi
nokori health
```

---

## From PyPI (recommended: local semantic retrieval)

This path runs semantic retrieval on your own machine, no embedding API key required. It installs **sentence-transformers** and, on `nokori install`, prefetches the local embedding model **[IBM Granite Embedding 97M](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2)** (`ibm-granite/granite-embedding-97m-multilingual-r2`) into `~/.nokori/models/`: **97M params / 384-dim**, ~**220MB** download.

After installing via **uv tool**, **pipx**, or **venv** above:

```bash
# Register hooks
nokori install              # Claude Code  → ~/.claude/settings.json
nokori install --cursor     # native Cursor only → ~/.cursor/hooks.json
nokori install --pi         # Pi only  -> ~/.pi/agent/extensions/nokori.ts
nokori install --omp        # OMP only -> ~/.omp/agent/extensions/nokori.ts
nokori install --all        # Claude Code + Cursor

# Verify (hooks.pi / hooks.omp are shown when installed)
nokori health
nokori status
```

Common side branches:

- **Skip weight download**: `nokori install --no-prefetch-embed`
- **Download manually / retry**: `nokori embed prefetch`
- **Debug hooks**: set `log_level = "info"` in `config.toml`, or `export NOKORI_LOG_LEVEL=info`

---

## Minimal install (no local model)

```bash
uv tool install nokori
nokori install
```

BM25 keyword retrieval works out of the box and is plenty. When you want semantic retrieval, either point at any OpenAI-compatible embedding API (set `NOKORI_EMBED_BASE_URL`, `NOKORI_EMBED_MODEL`) or reinstall the tool with `uv tool install --force "nokori[local-embed]"`.

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

## Claude Code, Cursor, Pi, and OMP

**Claude Code** stays the default. **Cursor** keeps its native and import paths. **Pi** and **OMP** install small TypeScript extension bridges at `~/.pi/agent/extensions/nokori.ts` and `~/.omp/agent/extensions/nokori.ts`, forwarding runtime events into the same Python dispatcher Nokori already uses elsewhere.

### Which install command?

Pi and OMP are explicit: `--all` still means Claude Code + Cursor only.

| Goal | Command | Writes |
|------|---------|--------|
| Claude Code only | `nokori install` | `~/.claude/settings.json` |
| Cursor only (native `~/.cursor/hooks.json`) | `nokori install --cursor` | `~/.cursor/hooks.json` |
| Pi only | `nokori install --pi` | `~/.pi/agent/extensions/nokori.ts` |
| OMP only | `nokori install --omp` | `~/.omp/agent/extensions/nokori.ts` |
| Claude Code + Cursor | `nokori install --all` | both files above |

### Verify Pi / OMP install

- Preview the write first if you want: `nokori install --pi --dry-run` or `nokori install --omp --dry-run`
- Run `nokori health` and confirm `hooks.pi` or `hooks.omp` reports `ok registered`
- Start a fresh session. Recall is injected on `before_agent_start`, Gate checks run on `tool_call`, and post-session extract starts from `session_shutdown` using the current session file from the runtime's session manager.
- Pi's `/reload` lifecycle is ignored by the bridge, so reloading extensions does not end or extract the active Nokori session.
- If `PI_CODING_AGENT_DIR` is set, `nokori install --pi` and transcript validation use that directory instead of `~/.pi/agent`.

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
# uv tool
uv tool upgrade nokori

# pipx
pipx upgrade nokori

# pip (inside venv)
pip install --upgrade nokori

# from source
git pull && pip install -e ".[local-embed,dev]"
```

After upgrading, run `nokori health` to confirm everything still checks out. Claude Code and Cursor hook registrations are stable across upgrades. If `hooks.pi` or `hooks.omp` reports a stale generated bridge, refresh it with `nokori install --pi` or `nokori install --omp` respectively.
