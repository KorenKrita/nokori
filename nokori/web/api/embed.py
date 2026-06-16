from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from nokori.search import embed_ipc
from nokori.web.deps import get_config, require_write_auth

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/embed/status")
def embed_status() -> dict:
    cfg = get_config()
    st = embed_ipc.server_status(cfg)
    from nokori.search.embedding import (
        local_embed_package_available,
        local_model_cached,
    )

    return {
        "data": {
            "running": st["running"],
            "pid": st["pid"],
            "idle_seconds": st["idle_seconds"],
            "socket": st.get("socket"),
            "package_installed": local_embed_package_available(),
            "model_cached": local_model_cached(cfg),
        }
    }


@router.post("/embed/start", dependencies=[Depends(require_write_auth)])
def embed_start() -> dict:
    cfg = get_config()
    st = embed_ipc.server_status(cfg)
    if st["running"]:
        return {"data": {"action": "already_running", "pid": st["pid"]}}

    from nokori.search.embedding import (
        local_embed_package_available,
        local_model_cached,
        prefetch_local_model,
    )

    if not local_embed_package_available():
        raise HTTPException(
            400,
            detail="sentence-transformers not installed. Run: pip install nokori[local-embed]",
        )

    if not local_model_cached(cfg):
        try:
            prefetch_local_model(cfg)
        except Exception:
            log.exception("model download failed")
            raise HTTPException(500, detail="model download failed")

    from nokori.search.embed_ipc import kickstart_server

    kickstart_server(cfg)

    for _ in range(20):
        time.sleep(0.5)
        st = embed_ipc.server_status(cfg)
        if st["running"]:
            return {"data": {"action": "started", "pid": st["pid"]}}

    return {"data": {"action": "starting", "pid": None}}


@router.post("/embed/stop", dependencies=[Depends(require_write_auth)])
def embed_stop() -> dict:
    cfg = get_config()
    st = embed_ipc.server_status(cfg)
    if not st["running"]:
        return {"data": {"action": "already_stopped"}}

    from nokori.search.embed_ipc import stop_server

    stop_server(cfg)
    return {"data": {"action": "stopped"}}
