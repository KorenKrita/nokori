"""Session registry atomic writes (parallel hooks must not clobber)."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from nokori.config import Config
from nokori.utils import sessions


def test_parallel_register_no_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()

    def _register(i: int) -> None:
        sessions.register(cfg, f"session-{i}", project_id="p")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_register, range(20)))

    files = list(cfg.sessions_dir.glob("*.json"))
    assert len(files) == 20
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data.get("session_id")
        assert data.get("started_at")


def test_end_writes_without_prior_register(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sessions.end(cfg, "orphan-session")
    p = cfg.sessions_dir / "orphan-session.json"
    assert not p.exists()
