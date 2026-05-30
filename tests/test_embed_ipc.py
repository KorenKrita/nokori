import json
import socket
import threading
from pathlib import Path

import pytest

from nokori.config import Config
from nokori.search import embed_ipc


def _run_fake_server(sock_path: Path, stop_event: threading.Event) -> None:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass
    srv.bind(str(sock_path))
    srv.listen(4)

    def serve():
        while not stop_event.is_set():
            srv.settimeout(0.3)
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            with conn:
                buf = b""
                while b"\n" not in buf:
                    part = conn.recv(4096)
                    if not part:
                        break
                    buf += part
                req = json.loads(buf.decode().split("\n", 1)[0])
                op = req.get("op")
                if op == "ping":
                    payload = {"ok": True, "op": "ping"}
                elif op == "shutdown":
                    payload = {"ok": True, "op": "shutdown"}
                    stop_event.set()
                elif op == "embed":
                    payload = {"ok": True, "op": "embed", "vectors": [[0.1, 0.2]]}
                else:
                    payload = {"ok": False, "error": "unknown"}
                conn.sendall((json.dumps(payload) + "\n").encode())
        srv.close()

    threading.Thread(target=serve, daemon=True).start()


def test_embed_ipc_ping_and_shutdown(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    short_sock = Path("/tmp") / f"nokori-{tmp_path.name[:8]}.sock"
    monkeypatch.setattr(embed_ipc, "socket_path", lambda c: short_sock)
    stop = threading.Event()
    _run_fake_server(short_sock, stop)
    assert embed_ipc.ping(cfg, timeout=1.0)
    assert embed_ipc.embed_text(cfg, "hello", timeout=1.0)
    embed_ipc.stop_server(cfg)
    stop.wait(timeout=2.0)


def test_sessions_active_idle(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_SESSION_IDLE_SECONDS", "60")
    cfg = Config.from_env()
    from nokori.utils import sessions
    sessions.register(cfg, "s1", "proj")
    assert sessions.count_active_sessions(cfg) == 1
    sessions.end(cfg, "s1")
    assert sessions.count_active_sessions(cfg) == 0

    sessions.register(cfg, "s2", "proj")
    data = json.loads((cfg.sessions_dir / "s2.json").read_text(encoding="utf-8"))
    data["last_activity"] = "2000-01-01T00:00:00Z"
    (cfg.sessions_dir / "s2.json").write_text(json.dumps(data), encoding="utf-8")
    assert sessions.count_active_sessions(cfg) == 0

    sessions.touch(cfg, "s3")
    assert sessions.count_active_sessions(cfg, exclude_session="other") >= 1
