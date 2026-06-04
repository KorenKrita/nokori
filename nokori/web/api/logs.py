from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nokori.web.deps import get_config

router = APIRouter()

LOG_FILES = ["hook.log", "pipeline.log", "async-extract.log", "embed-server.log"]
MAX_LOG_WS_CONNECTIONS = 5
LOG_WS_IDLE_TIMEOUT_SECONDS = 300.0
_active_log_ws_connections = 0


def _find_log_files(logs_dir: Path) -> list[Path]:
    """Find all readable log files in the logs directory."""
    files = []
    for name in LOG_FILES:
        p = logs_dir / name
        if p.exists():
            files.append(p)
    return files


@router.websocket("/logs")
async def logs_ws(ws: WebSocket):
    global _active_log_ws_connections
    if _active_log_ws_connections >= MAX_LOG_WS_CONNECTIONS:
        await ws.close(code=1013)
        return

    _active_log_ws_connections += 1
    file_handles: list[tuple[str, object]] = []
    try:
        await ws.accept()
        cfg = get_config()
        logs_dir = cfg.logs_dir

        level_filter: str | None = None
        try:
            msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            level_filter = msg.get("level")
        except (asyncio.TimeoutError, WebSocketDisconnect):
            pass

        log_files = _find_log_files(logs_dir)
        if not log_files:
            await ws.send_json({"type": "info", "line": f"No log files in {logs_dir}"})
            idle_started_at = time.monotonic()
            try:
                while True:
                    await asyncio.sleep(5)
                    log_files = _find_log_files(logs_dir)
                    if log_files:
                        break
                    if time.monotonic() - idle_started_at >= LOG_WS_IDLE_TIMEOUT_SECONDS:
                        await ws.close(code=1000)
                        return
            except WebSocketDisconnect:
                return

        # Send recent lines from all log files
        all_lines: list[tuple[float, str, str]] = []
        for lf in log_files:
            try:
                lines = lf.read_text().splitlines()[-30:]
                mtime = lf.stat().st_mtime
                for line in lines:
                    all_lines.append((mtime, f"[{lf.stem}] {line}", line))
            except OSError:
                continue

        all_lines.sort(key=lambda x: x[0])
        for _, display_line, raw in all_lines[-50:]:
            if level_filter and level_filter != "all" and level_filter not in raw.lower():
                continue
            await ws.send_json({"type": "log", "line": display_line})

        # Tail all files
        for lf in log_files:
            try:
                f = open(lf, "r")
                f.seek(0, 2)
                file_handles.append((lf.stem, f))
            except OSError:
                continue

        try:
            idle_started_at = time.monotonic()
            while True:
                found_any = False
                for stem, f in file_handles:
                    line = f.readline()
                    if line:
                        found_any = True
                        line = line.rstrip("\n")
                        if (
                            level_filter
                            and level_filter != "all"
                            and level_filter not in line.lower()
                        ):
                            continue
                        await ws.send_json({"type": "log", "line": f"[{stem}] {line}"})
                if not found_any:
                    if time.monotonic() - idle_started_at >= LOG_WS_IDLE_TIMEOUT_SECONDS:
                        await ws.close(code=1000)
                        return
                    await asyncio.sleep(0.3)
                else:
                    idle_started_at = time.monotonic()
        except (WebSocketDisconnect, OSError):
            pass
    finally:
        _active_log_ws_connections = max(0, _active_log_ws_connections - 1)
        for _, f in file_handles:
            try:
                f.close()
            except Exception:
                pass
