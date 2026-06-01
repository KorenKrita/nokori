from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nokori.web.deps import get_config

router = APIRouter()


@router.websocket("/logs")
async def logs_ws(ws: WebSocket):
    await ws.accept()
    cfg = get_config()
    log_path = cfg.logs_dir / "nokori.log"

    level_filter: str | None = None
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
        level_filter = msg.get("level")
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass

    if log_path.exists():
        lines = log_path.read_text().splitlines()[-50:]
        for line in lines:
            if level_filter and level_filter != "all" and level_filter not in line.lower():
                continue
            await ws.send_json({"type": "log", "line": line})

    try:
        with open(log_path, "r") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    line = line.rstrip("\n")
                    if level_filter and level_filter != "all" and level_filter not in line.lower():
                        continue
                    await ws.send_json({"type": "log", "line": line})
                else:
                    await asyncio.sleep(0.3)
    except (WebSocketDisconnect, OSError):
        pass
