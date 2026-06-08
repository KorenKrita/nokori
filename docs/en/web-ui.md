# Web UI Dashboard

[← Back to main README](../../README.md)

---

Nokori ships a built-in visual dashboard. One command and you're looking at everything.

```bash
nokori web                    # opens http://localhost:8765 in your browser
nokori web --port 9000        # custom port
nokori web --no-browser       # start server only, don't auto-open
```

---

## Pages

| Page | Content |
|------|---------|
| **Dashboard** | Rule counts by status, injection stats (24h), embed server status with start/stop control, gate state, extract pending jobs, lifecycle evidence |
| **Rules** | Filter list, detail page (trigger, action, evidence log, lifecycle evidence, replacement lineage), edit, dismiss |
| **Retrieve** | Enter a prompt, see exactly which rules fire: BM25 + embedding scores, HOT/WARM tier, matched tokens, shadow pool results |
| **Activity — Timeline** | Full event stream: hook calls, cold-pipeline decisions, lifecycle transitions, posthoc evaluations. Color-coded source labels, outcome badges, session/type filters |
| **Activity — Dashboard** | Operational charts: events-by-source bar chart, cold-pipeline conversion funnel, error pie chart, error trend line chart |
| **Injections** | Timeline of every rule injection, filterable by level or session |
| **Extract** | Pending/done jobs, extract state per transcript |
| **Lifecycle** | Evidence progress for candidate → active, active → trusted, and suppressed recovery |
| **Config** | Live view of all resolved config values + health checks |
| **Logs** | Real-time log stream via WebSocket, level filter, auto-scroll |

---

## Features

- **Multi-language**: auto-detects browser language, supports Chinese / English / Japanese, switchable in sidebar
- **Dark / Light mode**: follows system `prefers-color-scheme` by default, manual toggle in sidebar
- **Embed server control**: start/stop the local embedding server directly from the dashboard
- **Animations**: staggered card reveals, floating mesh gradient background, hover glow, spring physics buttons

---

## Frontend development

```bash
cd web
npm install
npm run dev          # Vite dev server :5173, proxies /api to :8765
# In another terminal:
nokori web --no-browser   # start the API backend
```
