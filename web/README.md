# Nokori Web UI

Frontend for the Nokori visual dashboard. See [Web UI documentation](../docs/en/web-ui.md) for features and usage.

## Development

```bash
cd web
npm install
npm run dev          # Vite dev server :5173, proxies /api to :8765
# In another terminal:
nokori web --no-browser   # start the API backend
```

## Design

See [DESIGN.md](./DESIGN.md) for the visual specification.
