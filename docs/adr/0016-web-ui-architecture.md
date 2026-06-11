# Web UI: FastAPI + React SPA with bundled static files

`nokori web` starts a uvicorn server serving both `/api/*` JSON routes and `/*` static files from `nokori/web/static/` (built React app).

Stack: FastAPI backend reusing existing db/search/config modules. React + Vite + Tailwind frontend with dark theme (OLED black #050505, glass morphism). WebSocket for real-time log tailing. Geist + Geist Mono fonts.

Development mode: Vite dev server proxies API to FastAPI. Production: frontend is pre-built and packaged inside the Python distribution.
