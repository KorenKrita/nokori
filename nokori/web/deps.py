from __future__ import annotations

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
