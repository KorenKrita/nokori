from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from nokori.config import Config
from nokori.db import Db, open_db

_cfg: Config | None = None


def set_config(cfg: Config) -> None:
    global _cfg
    _cfg = cfg


def get_config() -> Config:
    if _cfg is None:
        raise RuntimeError("config not initialized")
    return _cfg


def get_db() -> Db:
    cfg = get_config()
    return open_db(cfg.db_path)


WRITE_AUTH_COOKIE = "nokori_web_token"
WRITE_AUTH_HEADER = "x-nokori-web-token"


def new_write_auth_token() -> str:
    return secrets.token_urlsafe(32)


def require_write_auth(request: Request) -> None:
    """Require the process-local web write token for mutating endpoints."""
    expected = getattr(request.app.state, "write_auth_token", None)
    supplied = (
        request.headers.get(WRITE_AUTH_HEADER)
        or request.cookies.get(WRITE_AUTH_COOKIE)
    )
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="write authentication required")
