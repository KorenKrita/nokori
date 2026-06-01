from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nokori.search import embed_ipc
from nokori.web.deps import get_config

router = APIRouter()


@router.get("/embed/status")
def embed_status():
    cfg = get_config()
    st = embed_ipc.server_status(cfg)
    return {
        "data": {
            "running": st["running"],
            "pid": st["pid"],
            "idle_seconds": st["idle_seconds"],
            "socket": st.get("socket"),
        }
    }


@router.post("/embed/start")
def embed_start():
    cfg = get_config()
    st = embed_ipc.server_status(cfg)
    if st["running"]:
        return {"data": {"action": "already_running", "pid": st["pid"]}}

    from nokori.search.embedding import local_embed_capable
    if not local_embed_capable(cfg):
        raise HTTPException(400, detail="local embed not available (missing weights or package)")

    from nokori.search.embed_ipc import kickstart_server
    kickstart_server(cfg)

    import time
    for _ in range(10):
        time.sleep(0.5)
        st = embed_ipc.server_status(cfg)
        if st["running"]:
            return {"data": {"action": "started", "pid": st["pid"]}}

    return {"data": {"action": "starting", "pid": None}}


@router.post("/embed/stop")
def embed_stop():
    cfg = get_config()
    st = embed_ipc.server_status(cfg)
    if not st["running"]:
        return {"data": {"action": "already_stopped"}}

    from nokori.search.embed_ipc import stop_server
    stop_server(cfg)
    return {"data": {"action": "stopped"}}
