from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from nokori.config import Config

from .deps import WRITE_AUTH_COOKIE, new_write_auth_token, set_config


def create_app(cfg: Config) -> FastAPI:
    set_config(cfg)
    app = FastAPI(title="Nokori", docs_url=None, redoc_url=None)
    app.state.write_auth_token = new_write_auth_token()

    @app.middleware("http")
    async def write_auth_cookie(request: Request, call_next):
        response = await call_next(request)
        if request.method in {"GET", "HEAD"} and not request.cookies.get(WRITE_AUTH_COOKIE):
            response.set_cookie(
                WRITE_AUTH_COOKIE,
                app.state.write_auth_token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
        return response

    from .api import (
        config_api,
        dashboard,
        embed,
        extract,
        health,
        injections,
        lifecycle,
        logs,
        retrieve,
        rules,
    )

    app.include_router(dashboard.router, prefix="/api")
    app.include_router(rules.router, prefix="/api")
    app.include_router(retrieve.router, prefix="/api")
    app.include_router(injections.router, prefix="/api")
    app.include_router(extract.router, prefix="/api")
    app.include_router(lifecycle.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(embed.router, prefix="/api")
    app.include_router(logs.router, prefix="/api")

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

        index_html = static_dir / "index.html"
        static_root = static_dir.resolve()

        @app.get("/{path:path}")
        async def spa_fallback(request: Request, path: str) -> Response:
            file_path = (static_dir / path).resolve()
            inside_static = file_path.is_relative_to(static_root)
            if inside_static and file_path.is_file() and not path.startswith("api"):
                return FileResponse(file_path)
            return FileResponse(index_html)
    else:

        @app.get("/")
        async def web_ui_missing() -> Response:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "web_ui_not_packaged",
                    "detail": (
                        "Web UI static files are missing from this install "
                        "(PyPI wheels before 0.2.4 did not bundle nokori/web/static). "
                        "Upgrade to nokori>=0.2.4, or install from source after "
                        "`cd web && npm ci && npm run build`."
                    ),
                },
            )

    return app
