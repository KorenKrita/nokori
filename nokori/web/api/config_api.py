from __future__ import annotations

from fastapi import APIRouter

from nokori.web.deps import get_config

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
        }
    }
