from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nokori.config import Config
from nokori.config_editor import get_editor_state, save_editor
from nokori.errors import ConfigError
from nokori.web.deps import get_config, require_write_auth, set_config

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/config")
def show_config():
    cfg = get_config()
    return {
        "data": {
            "data_dir": str(cfg.data_dir),
            "db_path": str(cfg.db_path),
            "disabled": cfg.disabled,
            "gate_enabled": cfg.gate_enabled,
            "gate_ttl_seconds": cfg.gate_ttl_seconds,
            "gate_matcher": cfg.gate_matcher,
            "extract_mode": cfg.extract_mode,
            "llm_base_url": cfg.llm_base_url or None,
            "llm_model": cfg.llm_model or None,
            "embed_enabled": cfg.embed_enabled,
            "embed_base_url": cfg.embed_base_url or None,
            "embed_model": cfg.embed_model or None,
            "embed_hook_timeout_seconds": cfg.embed_hook_timeout_seconds,
            "hot_cache_enabled": cfg.hot_cache_enabled,
            "session_idle_seconds": cfg.session_idle_seconds,
            "promotion_enabled": cfg.promotion_enabled,
            "log_level": cfg.log_level,
            "role_models": cfg.role_models or {},
        }
    }


@router.get("/config/editor")
def config_editor_get(locale: str | None = Query(None)):
    cfg = get_config()
    return {"data": get_editor_state(cfg, locale)}


class ConfigEditorSave(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    embed_mode: Literal["local", "remote"] | None = None
    set_keys: list[str] = Field(default_factory=list)


@router.put("/config/editor", dependencies=[Depends(require_write_auth)])
def config_editor_put(body: ConfigEditorSave):
    cfg = get_config()
    try:
        result = save_editor(
            cfg,
            values=body.values,
            embed_mode=body.embed_mode,
            initial_set_keys=set(body.set_keys),
        )
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        set_config(Config.from_env())
    except ConfigError as e:
        log.exception("config reload failed")
        raise HTTPException(status_code=500, detail="config reload failed") from e
    return {"data": result}
