from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from nokori.config import Config

from .deps import set_config


def create_app(cfg: Config) -> FastAPI:
    set_config(cfg)
    app = FastAPI(title="Nokori", docs_url=None, redoc_url=None)

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

        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
