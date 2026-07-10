"""Blocking Unix-socket server for local embeddings (one loaded model per process)."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any, cast

from ..config import Config
from ..utils import file_lock
from ..utils.logging import get_logger
from . import embed_ipc
from .embedding import EmbedKind, LocalEmbeddingClient

log = get_logger("nokori.search.embedding_server")

_MAX_REQUEST_BYTES = 1 << 20  # 1 MiB per JSON-line request


def _reply(conn: socket.socket, payload: dict[str, Any]) -> None:
    conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def _handle_connection(
    conn: socket.socket,
    client: LocalEmbeddingClient,
) -> bool:
    """Returns False to shut down the server."""
    try:
        with conn:
            conn.settimeout(30.0)
            buf = b""
            while b"\n" not in buf:
                if len(buf) >= _MAX_REQUEST_BYTES:
                    _reply(conn, {"ok": False, "error": "request too large"})
                    return True
                part = conn.recv(65536)
                if not part:
                    return True
                buf += part
            line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
            req = json.loads(line)
            op = req.get("op")
            if op == "ping":
                _reply(conn, {"ok": True, "op": "ping"})
            elif op == "shutdown":
                _reply(conn, {"ok": True, "op": "shutdown"})
                return False
            elif op == "embed":
                text = req.get("text") or ""
                raw_kind = req.get("kind") or "document"
                if raw_kind not in ("query", "document"):
                    raw_kind = "document"
                vectors = client.embed(text, kind=cast(EmbedKind, raw_kind))
                _reply(conn, {"ok": True, "op": "embed", "vectors": vectors})
            else:
                _reply(conn, {"ok": False, "error": f"unknown op: {op!r}"})
    except (OSError, json.JSONDecodeError, ValueError) as e:
        with contextlib.suppress(OSError):
            _reply(conn, {"ok": False, "error": str(e)})
    return True


def _cleanup(sock: socket.socket | None, sock_path: Path, cfg: Config) -> None:
    if sock is not None:
        sock.close()
    embed_ipc._clear_pid(cfg)
    with contextlib.suppress(FileNotFoundError):
        sock_path.unlink()


def run_server(cfg: Config) -> int:
    """CLI entry: ``nokori embed serve``."""
    cfg.ensure_dirs()
    with file_lock.acquire(embed_ipc.server_lock_path(cfg), label="embed server") as acquired:
        if not acquired:
            log.info("embed server already running or starting")
            return 0
        return _run_server_locked(cfg)


def _run_server_locked(cfg: Config) -> int:
    if embed_ipc.ping(cfg):
        return 0
    existing_pid = embed_ipc._read_pid(cfg)
    if (
        existing_pid is not None
        and existing_pid != os.getpid()
        and embed_ipc._pid_alive(existing_pid)
        and embed_ipc._is_embed_server_process(existing_pid)
    ):
        log.warning("legacy embed server process is still starting pid=%s", existing_pid)
        return 0
    embed_ipc.cleanup_stale(cfg, force=True)
    sock_path = embed_ipc.socket_path(cfg)
    sock: socket.socket | None = None
    embed_ipc._write_pid(cfg, os.getpid())

    def _terminate(_signum: int, _frame: Any) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _terminate)

    try:
        client = LocalEmbeddingClient(cfg)
        if not client.available():
            log.error("sentence-transformers not available; embed server exiting")
            return 1

        try:
            client.load_model()
        except Exception:
            log.exception("embed server model load failed")
            return 1

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with contextlib.suppress(FileNotFoundError):
            sock_path.unlink()
        sock.bind(str(sock_path))
        try:
            os.chmod(sock_path, 0o600)
        except OSError as e:
            log.warning("could not chmod embed socket %s: %s", sock_path, e)
        sock.listen(8)

        last_activity = time.monotonic()
        idle_limit = float(cfg.embed_server_idle_seconds)
        log.info("embed server listening on %s (idle=%ss)", sock_path, int(idle_limit))

        while True:
            sock.settimeout(1.0)
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                if time.monotonic() - last_activity >= idle_limit:
                    log.info("embed server idle timeout (%ss)", int(idle_limit))
                    break
                continue
            if not _handle_connection(conn, client):
                break
            last_activity = time.monotonic()
        return 0
    finally:
        _cleanup(sock, sock_path, cfg)
