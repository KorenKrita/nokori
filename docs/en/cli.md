# CLI Reference

[← Back to main README](../../README.md)

---

## Rule management

```bash
nokori add --trigger "..." --action "..." [--severity reminder|high_risk] [--variants ...] [--terms-en ...] [--terms-zh ...] [--project-id ...]
nokori list [--all] [--project <id>]
nokori show <short_id>
nokori dismiss <short_id>
nokori edit <short_id> [--trigger ...] [--action ...] [--variants ...] [--terms-en ...] [--terms-zh ...]
```

---

## Extraction

```bash
nokori extract [--session <path>] [--dry-run]
nokori extract                    # Consume all pending jobs
```

---

## Debugging

```bash
nokori test "<prompt>" [--project <id>]
nokori status                     # Rule status, hook/config, embed, and lifecycle evidence
nokori logs
nokori health
```

---

## Observability (AI-friendly)

```bash
nokori report [--since <ISO>] [--session <id>] [--json]
nokori stream [--since <ISO>] [--session <id>] [--type <source>] [--verbose] [--follow]
```

---

## Maintenance

```bash
nokori maintain
```

---

## Local embed

```bash
nokori embed prefetch | start | stop | status
```

---

## Import / export

```bash
nokori export <path.json>
nokori import <path.json>
```

JSON `version` field = rules.db schema, currently 2.

---

## Installation management

```bash
nokori install [--claude | --cursor | --all]
               [--dry-run | --uninstall | --disable | --enable | --no-prefetch-embed]
```

---

## Web UI

```bash
nokori web                        # Opens http://localhost:8765 in your browser
nokori web --port 9000            # Custom port
nokori web --no-browser           # Start server only
```
