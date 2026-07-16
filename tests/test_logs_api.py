"""Tests for logs API helpers and WebSocket /api/logs."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("httpx2")

from fastapi.testclient import TestClient

from nokori.config import Config
from nokori.web.api.logs import LOG_FILES, _find_log_files
from nokori.web.app import create_app


def test_find_log_files_only_existing(tmp_path: Path) -> None:
    (tmp_path / "hook.log").write_text("a\n")
    (tmp_path / "pipeline.log").write_text("b\n")
    (tmp_path / "noise.txt").write_text("x\n")
    found = _find_log_files(tmp_path)
    names = {p.name for p in found}
    assert names == {"hook.log", "pipeline.log"}
    assert all(name in LOG_FILES for name in names)


def test_find_log_files_empty_dir(tmp_path: Path) -> None:
    assert _find_log_files(tmp_path) == []


def test_logs_ws_streams_existing_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "hook.log").write_text("INFO hello from hook\nWARN something\n")

    base = Config.from_env()
    cfg = replace(base, data_dir=tmp_path)
    client = TestClient(create_app(cfg))
    with client.websocket_connect("/api/logs") as ws:
        ws.send_json({"level": "all"})
        # Exactly two historical lines — avoid blocking on the live-tail loop.
        first = ws.receive_json()
        second = ws.receive_json()
    lines = [m.get("line", "") for m in (first, second) if m.get("type") == "log"]
    assert any("hello from hook" in line for line in lines)
    assert any("WARN something" in line for line in lines)


def test_logs_ws_info_when_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    base = Config.from_env()
    cfg = replace(base, data_dir=tmp_path)
    (tmp_path / "logs").mkdir(exist_ok=True)
    client = TestClient(create_app(cfg))
    with client.websocket_connect("/api/logs") as ws:
        msg = ws.receive_json()
    assert msg.get("type") == "info"
    assert "No log files" in msg.get("line", "")
