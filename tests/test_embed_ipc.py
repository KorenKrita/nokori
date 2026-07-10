import contextlib
import json
import os
import socket
import threading
from pathlib import Path
from unittest.mock import patch

from nokori.config import Config
from nokori.search import embed_ipc


def _run_fake_server(sock_path: Path, stop_event: threading.Event) -> None:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        sock_path.unlink()
    srv.bind(str(sock_path))
    srv.listen(4)

    def serve():
        while not stop_event.is_set():
            srv.settimeout(0.3)
            try:
                conn, _ = srv.accept()
            except TimeoutError:
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


def test_cleanup_stale_preserves_server_that_is_starting(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    embed_ipc._write_pid(cfg, os.getpid())
    sock = embed_ipc.socket_path(cfg)
    sock.write_text("starting")
    monkeypatch.setattr(embed_ipc.file_lock, "is_locked", lambda path: True)
    monkeypatch.setattr(embed_ipc, "ping", lambda c, **kw: False)

    embed_ipc.cleanup_stale(cfg)

    assert embed_ipc.pid_path(cfg).exists()
    assert sock.exists()


def test_spawn_server_skips_when_start_lock_is_held(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    monkeypatch.setattr(embed_ipc, "ping", lambda c, **kw: False)
    monkeypatch.setattr(embed_ipc.file_lock, "is_locked", lambda path: True)

    with patch("subprocess.Popen") as popen:
        embed_ipc.spawn_server(cfg)

    popen.assert_not_called()


def test_stop_server_never_signals_unrelated_stale_pid(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    embed_ipc._write_pid(cfg, os.getpid())
    monkeypatch.setattr(embed_ipc, "ping", lambda c, **kw: False)
    monkeypatch.setattr(embed_ipc, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(embed_ipc, "_is_embed_server_process", lambda pid: False)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(embed_ipc.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    assert embed_ipc.stop_server(cfg) is True
    assert signals == []
    assert not embed_ipc.pid_path(cfg).exists()


def test_embed_process_identity_requires_exact_serve_command(monkeypatch):
    monkeypatch.setattr(
        embed_ipc,
        "_process_command",
        lambda pid: "/usr/bin/python -m nokori embed serve",
    )
    assert embed_ipc._is_embed_server_process(123)
    monkeypatch.setattr(embed_ipc, "_process_command", lambda pid: "/usr/bin/python worker.py")
    assert not embed_ipc._is_embed_server_process(123)


def test_run_server_skips_when_another_server_holds_lock(monkeypatch, tmp_path):
    from nokori.search import embedding_server

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()

    @contextlib.contextmanager
    def _busy_lock(*args, **kwargs):
        yield False

    monkeypatch.setattr(embedding_server.file_lock, "acquire", _busy_lock)
    monkeypatch.setattr(
        embedding_server,
        "LocalEmbeddingClient",
        lambda cfg: (_ for _ in ()).throw(AssertionError("model should not load")),
    )

    assert embedding_server.run_server(cfg) == 0


def test_kickstart_spawns_without_blocking(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    spawned: list[int] = []
    monkeypatch.setattr(embed_ipc, "ping", lambda c, **kw: False)
    monkeypatch.setattr(embed_ipc, "spawn_server", lambda c: spawned.append(1))
    assert embed_ipc.kickstart_server(cfg) is False
    assert spawned == [1]


def test_hook_search_skips_embed_when_server_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    from nokori.db import open_db
    from nokori.models import Rule
    from nokori.search import embedding

    rule = Rule(
        id="r1",
        short_id="r1abcd",
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="v1",
        runtime_policy_version="v1",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical="git push force",
        trigger_variants=[],
        search_terms={},
        action_instruction="do not",
        source_origin="correction",
        project_scope="project",
        project_id="p1",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    db = open_db(cfg.db_path)
    try:
        waited: list[float] = []

        def fake_ensure(*a, **kw):
            waited.append(kw.get("max_wait", 0))
            return True

        monkeypatch.setattr(embedding, "_sentence_transformers_available", lambda: True)
        monkeypatch.setattr(embedding, "local_model_cached", lambda c: True)
        monkeypatch.setattr(embedding, "local_embed_package_available", lambda: True)
        monkeypatch.setattr(embed_ipc, "kickstart_server", lambda c: False)
        monkeypatch.setattr(embed_ipc, "ensure_running", fake_ensure)
        results, mode = embedding.search_local_shared(
            "git push", [rule], db, cfg, interaction="hook"
        )
        assert results == [] and mode == "off"
        assert not waited
    finally:
        db.close()


