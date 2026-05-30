"""Unix-socket IPC for a shared local embedding server (one model, all hook processes)."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..config import Config
from ..utils.logging import get_logger

log = get_logger("nokori.search.embed_ipc")

_STARTUP_WAIT_SECONDS = 45.0
_STARTUP_POLL_SECONDS = 0.15
_MAX_IPC_RESPONSE_BYTES = 1 << 20  # 1 MiB cap per JSON-line response


def socket_path(cfg: Config) -> Path:
    return cfg.data_dir / "embed.sock"


def pid_path(cfg: Config) -> Path:
    return cfg.data_dir / "embed-server.pid"


def _read_pid(cfg: Config) -> int | None:
    p = pid_path(cfg)
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _write_pid(cfg: Config, pid: int) -> None:
    pid_path(cfg).write_text(str(pid), encoding="utf-8")


def _clear_pid(cfg: Config) -> None:
    try:
        pid_path(cfg).unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_stale(cfg: Config) -> None:
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid):
        return
    if pid is not None:
        _clear_pid(cfg)
    sock = socket_path(cfg)
    try:
        sock.unlink()
    except FileNotFoundError:
        pass


def request(cfg: Config, payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
    """Send one JSON-line request to the embed server."""
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(socket_path(cfg)))
        sock.sendall(data)
        chunks: list[bytes] = []
        total = 0
        while True:
            part = sock.recv(65536)
            if not part:
                break
            total += len(part)
            if total > _MAX_IPC_RESPONSE_BYTES:
                raise OSError("embed server response too large")
            chunks.append(part)
            if b"\n" in part:
                break
    finally:
        sock.close()
    if not chunks:
        raise OSError("embed server closed connection")
    line = b"".join(chunks).split(b"\n", 1)[0].decode("utf-8", errors="replace")
    return json.loads(line)


def ping(cfg: Config, *, timeout: float = 0.5) -> bool:
    try:
        resp = request(cfg, {"op": "ping"}, timeout=timeout)
        return bool(resp.get("ok"))
    except (OSError, json.JSONDecodeError, KeyError):
        return False


def spawn_server(cfg: Config) -> None:
    """Start detached embed server if not already running."""
    cleanup_stale(cfg)
    if ping(cfg):
        return
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid):
        return

    cfg.ensure_dirs()
    err_log = cfg.logs_dir / "embed-server.log"
    err_fh = subprocess.DEVNULL
    try:
        err_fh = open(err_log, "a", encoding="utf-8")
    except OSError:
        err_fh = subprocess.DEVNULL
    env = os.environ.copy()
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "nokori", "embed", "serve"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
            start_new_session=True,
        )
    finally:
        if err_fh is not subprocess.DEVNULL:
            try:
                err_fh.close()
            except OSError:
                pass


def kickstart_server(cfg: Config) -> bool:
    """Return True if embed server is up now.

    On hook path: spawn detached server when auto_start is enabled but do not
    block waiting for model load — caller falls back to BM25 for this turn.
    """
    if ping(cfg):
        return True
    if cfg.embed_server_auto_start:
        spawn_server(cfg)
        log.info("embed server spawn requested (not waiting on hook path)")
    return False


def ensure_running(cfg: Config, *, max_wait: float | None = None) -> bool:
    """Ping or auto-start the embed server; wait until ready or timeout (CLI/index)."""
    if not cfg.embed_server_auto_start:
        return ping(cfg)
    if ping(cfg):
        return True
    spawn_server(cfg)
    deadline = time.monotonic() + (max_wait if max_wait is not None else _STARTUP_WAIT_SECONDS)
    while time.monotonic() < deadline:
        if ping(cfg):
            return True
        time.sleep(_STARTUP_POLL_SECONDS)
    return False


def stop_server(cfg: Config) -> bool:
    """Graceful shutdown via IPC; SIGTERM stale pid if socket is dead."""
    if ping(cfg, timeout=1.0):
        try:
            request(cfg, {"op": "shutdown"}, timeout=3.0)
            for _ in range(30):
                if not ping(cfg, timeout=0.3):
                    cleanup_stale(cfg)
                    return True
                time.sleep(0.1)
        except (OSError, json.JSONDecodeError):
            pass
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(30):
            if not _pid_alive(pid):
                break
            time.sleep(0.1)
    cleanup_stale(cfg)
    return not ping(cfg, timeout=0.3)


def server_status(cfg: Config) -> dict[str, Any]:
    alive = ping(cfg, timeout=0.5)
    pid = _read_pid(cfg)
    return {
        "running": alive,
        "pid": pid if pid is not None and _pid_alive(pid) else None,
        "socket": str(socket_path(cfg)),
        "idle_seconds": cfg.embed_server_idle_seconds,
    }


def embed_text(
    cfg: Config,
    text: str,
    *,
    timeout: float = 5.0,
    auto_start: bool = True,
) -> list[list[float]]:
    """Encode text via the shared server. Returns [] on failure."""
    if auto_start:
        if not ensure_running(cfg, max_wait=_STARTUP_WAIT_SECONDS):
            return []
    elif not ping(cfg, timeout=min(timeout, 1.0)):
        return []
    try:
        resp = request(
            cfg,
            {"op": "embed", "text": text},
            timeout=timeout,
        )
    except (OSError, json.JSONDecodeError) as e:
        log.warning("embed IPC failed: %s", e)
        return []
    if not resp.get("ok"):
        log.warning("embed IPC error: %s", resp.get("error"))
        return []
    vectors = resp.get("vectors") or []
    return [list(v) for v in vectors]


def run_server(cfg: Config) -> int:
    """Delegate to ``embedding_server`` (kept for backward-compatible imports)."""
    from .embedding_server import run_server as _run_blocking_server

    return _run_blocking_server(cfg)
