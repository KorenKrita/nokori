"""Unix-socket IPC for a shared local embedding server (one model, all hook processes)."""

from __future__ import annotations

import contextlib
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
from ..utils import file_lock
from ..utils.fs import atomic_write_text
from ..utils.logging import get_logger

log = get_logger("nokori.search.embed_ipc")

_STARTUP_WAIT_SECONDS = 45.0
_STARTUP_POLL_SECONDS = 0.15
_MAX_IPC_RESPONSE_BYTES = 1 << 20  # 1 MiB cap per JSON-line response


def socket_path(cfg: Config) -> Path:
    return cfg.data_dir / "embed.sock"


def pid_path(cfg: Config) -> Path:
    return cfg.data_dir / "embed-server.pid"


def server_lock_path(cfg: Config) -> Path:
    return cfg.data_dir / "embed-server.lock"


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
    atomic_write_text(pid_path(cfg), f"{pid}\n", mkdir=True)


def _clear_pid(cfg: Config) -> None:
    with contextlib.suppress(FileNotFoundError):
        pid_path(cfg).unlink()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_command(pid: int) -> str | None:
    ps = next((p for p in ("/bin/ps", "/usr/bin/ps") if Path(p).exists()), None)
    if ps is None:
        return None
    try:
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    command = result.stdout.strip()
    return command or None


def _is_embed_server_process(pid: int) -> bool:
    command = _process_command(pid)
    return command is not None and " -m nokori embed serve" in f" {command}"


def _server_lock_held(cfg: Config) -> bool:
    try:
        return file_lock.is_locked(server_lock_path(cfg))
    except OSError as e:
        log.warning("embed server lock check failed: %s", e)
        return True


def cleanup_stale(cfg: Config, *, force: bool = False) -> None:
    if not force and _server_lock_held(cfg):
        return
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid) and (ping(cfg) or _is_embed_server_process(pid)):
        return
    if pid is not None:
        _clear_pid(cfg)
    sock = socket_path(cfg)
    with contextlib.suppress(FileNotFoundError):
        sock.unlink()


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
    result: dict[str, Any] = json.loads(line)
    return result


def ping(cfg: Config, *, timeout: float = 0.5) -> bool:
    try:
        resp = request(cfg, {"op": "ping"}, timeout=timeout)
        return bool(resp.get("ok"))
    except (OSError, json.JSONDecodeError, KeyError):
        return False


def spawn_server(cfg: Config) -> None:
    """Start detached embed server if not already running or starting."""
    if ping(cfg) or _server_lock_held(cfg):
        return
    cleanup_stale(cfg)
    if ping(cfg) or _server_lock_held(cfg):
        return
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid) and _is_embed_server_process(pid):
        return

    cfg.ensure_dirs()
    err_log = cfg.logs_dir / "embed-server.log"
    env = os.environ.copy()
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)
    err_file = None
    try:
        with contextlib.suppress(OSError):
            err_file = open(err_log, "a", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(
            [sys.executable, "-m", "nokori", "embed", "serve"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_file if err_file is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        if err_file is not None:
            with contextlib.suppress(OSError):
                err_file.close()


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
    verified_server = pid is not None and _pid_alive(pid) and _is_embed_server_process(pid)
    if pid is not None and _pid_alive(pid):
        if verified_server:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
        else:
            log.warning("refusing to signal non-Nokori process from stale embed pid=%s", pid)
    if not verified_server or pid is None or not _pid_alive(pid):
        cleanup_stale(cfg, force=True)
    return not ping(cfg, timeout=0.3)


def server_status(cfg: Config) -> dict[str, Any]:
    alive = ping(cfg, timeout=0.5)
    pid = _read_pid(cfg)
    pid_alive = pid is not None and _pid_alive(pid)
    return {
        "running": alive,
        "starting": not alive and _server_lock_held(cfg),
        "pid": pid if pid_alive else None,
        "socket": str(socket_path(cfg)),
        "idle_seconds": cfg.embed_server_idle_seconds,
    }


def embed_text(
    cfg: Config,
    text: str,
    *,
    timeout: float = 5.0,
    auto_start: bool = True,
    kind: str = "document",
) -> list[list[float]]:
    """Encode text via the shared server. Returns [] on failure.

    ``kind`` must be ``query`` (user prompt) or ``document`` (indexed rules).
    """
    if kind not in ("query", "document"):
        kind = "document"
    if auto_start:
        if not ensure_running(cfg, max_wait=_STARTUP_WAIT_SECONDS):
            return []
    elif not ping(cfg, timeout=min(timeout, 1.0)):
        return []
    try:
        resp = request(
            cfg,
            {"op": "embed", "text": text, "kind": kind},
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
