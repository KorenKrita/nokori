# Nokori 残り

<p align="center">
  <img src="docs/assets/logo.png" width="160" height="160" alt="Nokori" />
</p>

<p align="center">
  <strong>A behavioral memory layer forged for Claude Code and Cursor.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/v/nokori" alt="PyPI" /></a>
  <a href="https://pypi.org/project/nokori/"><img src="https://img.shields.io/pypi/pyversions/nokori" alt="Python" /></a>
  <a href="https://github.com/KorenKrita/nokori/blob/main/LICENSE"><img src="https://img.shields.io/github/license/KorenKrita/nokori" alt="License" /></a>
  <a href="https://github.com/KorenKrita/nokori/stargazers"><img src="https://img.shields.io/github/stars/KorenKrita/nokori" alt="Stars" /></a>
</p>

<p align="center">
  <b>Languages:</b> <b>English</b> | <a href="README.zh-CN.md">简体中文</a> | <a href="README.zh-TW.md">繁體中文</a> | <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="#quick-install">Quick Install</a> · <a href="#one-minute-overview">How It Works</a> · <a href="docs/en/architecture.md">Architecture</a> · <a href="docs/en/configuration.md">Configuration</a> · <a href="docs/en/cli.md">CLI Reference</a> · <a href="docs/en/web-ui.md">Web UI</a>
</p>

---

> What experience leaves behind runs deeper than memory.

Nokori (残り) means what remains: the thing still standing in place after the noise dies down.

Every session ends, and every correction you made evaporates with it. In the next session the agent wakes a stranger again, the same stranger who force-pushes, forgets to run the migration, types a dangerous command straight at the production database.

Nokori refuses to let it forget. It settles every "don't do that" you ever said into recallable behavioral rules: when your words drift back toward that scene, the rule surfaces on its own inside the agent's context. New rules first live as candidates underwater, collecting evidence in the background. Only after the cold path and posthoc evidence trust them can the sharpest ones become Gate-eligible and block the first risky tool call before the agent touches your files.

Your data stays on your machine, in SQLite, the whole way through. Retrieval during a chat never touches a model. Only the post-session extract calls an LLM, and even then it is fed nothing but compressed session fragments. Want it fully offline? Point the endpoint at a local Ollama.

---

## Who is it for

- People who keep correcting the same class of problem: force pushes, forgotten migrations, commands fired at the wrong database
- People who want cross-project "don't do that" knowledge they build once and carry across repos, instead of re-teaching every repo from scratch
- People who trust local: rules sit in SQLite on your own machine, exportable anytime, whole chats never sent out

---

## One minute overview

```
You correct Claude / Cursor
    └─▶ Nokori carves a rule (what scene + what to do)
            └─▶ Next time your words drift near that scene
                    └─▶ The rule auto-writes into the agent's context (reminder)
                            └─▶ If it later becomes trusted + gate_eligible:
                                 block once before the first matching tool call (Gate)
```

During a chat Nokori only does retrieval and small file I/O, never making you wait on a model. The LLM is only called after the session closes, when it extracts new rules from the transcript at its own pace.

---

## Quick install

**Prerequisites**: Python >= 3.11, Claude Code or Cursor already installed

```bash
# Recommended: pipx with local semantic retrieval
brew install pipx && pipx ensurepath
pipx install "nokori[local-embed]"

# Register hooks
nokori install --all        # or --cursor / default is Claude Code only

# Verify
nokori health
```

<details>
<summary>Other install methods</summary>

```bash
# Minimal install (BM25 only, no local model)
pipx install nokori

# Dedicated venv
python3 -m venv ~/.local/venvs/nokori
~/.local/venvs/nokori/bin/pip install "nokori[local-embed]"
echo 'export PATH="$HOME/.local/venvs/nokori/bin:$PATH"' >> ~/.zshrc

# From source
git clone https://github.com/KorenKrita/nokori.git && cd nokori
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
```

</details>

> Full installation guide (Cursor config, updating, uninstalling) in [Installation](docs/en/installation.md)

---

## Quick start

```bash
# 1. Add a candidate rule
nokori add \
  --trigger "Force pushing to a shared branch" \
  --action "Use --force-with-lease, or push to a new branch" \
  --rationale "force push overwrites peers' work" \
  --source-type correction --confidence high

# 2. Verify the shadow match
nokori test "I'll just git push --force this branch"

# 3. Run maintenance (let evidence move rules forward)
nokori maintain

# 4. Rule out of date? Dismiss it
nokori dismiss <short_id>
```

Just open Claude Code or Cursor and work as usual. When a rule matches, the agent sees the injected reminder before it replies. For `trusted` + `gate_eligible` rules, the first sensitive tool call is blocked once.

---

## Core features

| Feature | Description |
|---------|-------------|
| **Autonomous quality flywheel** | candidate -> active -> trusted; rules must earn evidence before gaining authority |
| **Zero model calls on hot path** | Hooks do deterministic retrieval/matching/scoring only; no LLM wait between prompt and reply |
| **Hybrid retrieval** | BM25 out of the box + optional local/remote semantic vectors, RRF fusion |
| **Conservative Gate** | Only trusted + gate_eligible rules can block tools, and only once per turn |
| **Shadow evidence** | Candidates accumulate counterfactual evidence in the background without disturbing the current chat |
| **Local-first** | SQLite + filesystem, data never leaves your machine, optional offline LLM |
| **Cross-tool support** | Native support for both Claude Code and Cursor |
| **Web UI** | `nokori web` for a visual dashboard to manage all state |

---

## Documentation

| Document | Content |
|----------|---------|
| [Architecture](docs/en/architecture.md) | Flywheel mechanism, hook timing, injection vs Gate, Shadow Pool |
| [Installation](docs/en/installation.md) | Platform install, Cursor config, updating & uninstalling |
| [Configuration](docs/en/configuration.md) | config.toml, environment variables, full reference |
| [Retrieval Engine](docs/en/retrieval.md) | BM25, embedding, injection tiers |
| [Rule Lifecycle](docs/en/lifecycle.md) | State machine, promotion conditions, maintenance tasks |
| [Automatic Extraction](docs/en/extraction.md) | Cold-path pipeline, merge strategy, async mode |
| [Gate Mechanism](docs/en/gate.md) | Two-layer matching, configuration, prompt-hash safety |
| [CLI Reference](docs/en/cli.md) | All commands and options |
| [Web UI](docs/en/web-ui.md) | Visual dashboard features and development |

---

## Relationship with existing systems

| System | Relationship |
|--------|--------------|
| CLAUDE.md | Complementary. Nokori doesn't touch your CLAUDE.md; it handles the dynamic "when X, do Y" |
| Claude Code auto-memory | No conflict. Memory leans factual, Nokori leans behavioral rules |
| Other memory plugins | Hooks can coexist, but avoid stacking many context-injection plugins |

---

## Data storage

All data lives in one local directory, `~/.nokori/`. There is no network sync. Rules store behavioral descriptions, not your source code. Only the cold-path extract calls an LLM; point the endpoint at a local Ollama for fully offline operation.

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[local-embed,dev]"
python -m pytest tests/
```

Project constraints: core engine is pure stdlib + urllib, no LLM calls on the hot path, all hooks wrapped in top-level try/except fail-open.

---

## License

MIT
