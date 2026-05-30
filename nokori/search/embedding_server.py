"""Blocking Unix-socket server for local embeddings (one loaded model per process)."""
from __future__ import annotations

import json
import os
import socket
import time
from typing import Any

from ..config import Config
from ..utils.logging import get_logger
from .embedding import LocalEmbeddingClient
from . import embed_ipc

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
                vectors = client.embed(text)
                _reply(conn, {"ok": True, "op": "embed", "vectors": vectors})
            else:
                _reply(conn, {"ok": False, "error": f"unknown op: {op!r}"})
    except (OSError, json.JSONDecodeError, ValueError) as e:
        try:
            _reply(conn, {"ok": False, "error": str(e)})
        except OSError:
            pass
    return True


def run_server(cfg: Config) -> int:
    """CLI entry: ``nokori embed serve``."""
    embed_ipc.cleanup_stale(cfg)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_path = embed_ipc.socket_path(cfg)
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass
    sock.bind(str(sock_path))
    try:
        os.chmod(sock_path, 0o600)
    except OSError as e:
        log.warning("could not chmod embed socket %s: %s", sock_path, e)
    sock.listen(8)
    embed_ipc._write_pid(cfg, os.getpid())

    client = LocalEmbeddingClient(cfg)
    if not client.available():
        log.error("sentence-transformers not available; embed server exiting")
        sock.close()
        embed_ipc._clear_pid(cfg)
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        return 1

    try:
        model = client._load_model()
    except Exception:
        log.exception("embed server model load failed")
        sock.close()
        embed_ipc._clear_pid(cfg)
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        return 1

    last_activity = time.monotonic()
    idle_limit = float(cfg.embed_server_idle_seconds)
    log.info("embed server listening on %s (idle=%ss)", sock_path, int(idle_limit))

    try:
        while True:
            sock.settimeout(1.0)
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                if time.monotonic() - last_activity >= idle_limit:
                    log.info("embed server idle timeout (%ss)", int(idle_limit))
                    break
                continue
            if not _handle_connection(conn, client):
                break
            last_activity = time.monotonic()
    finally:
        sock.close()
        embed_ipc._clear_pid(cfg)
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        del model
    return 0
