"""Local embed prefetch, cache detection, and session_start kickstart (no ST in hook)."""
from dataclasses import replace
from unittest.mock import patch


from nokori.config import Config
from nokori.db import open_db
from nokori.hooks import session_start
from nokori.search import embedding


def test_local_model_cached_detects_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert embedding.local_model_cached(cfg) is False

    snap = (
        tmp_path
        / "models"
        / embedding.local_model_hub_dir(embedding.LOCAL_MODEL_HF_ID)
        / "snapshots"
        / "abc123"
    )
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"x")

    assert embedding.local_model_cached(cfg) is True


def test_embedding_active_local_without_import(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "1")
    cfg = Config.from_env()
    with patch("nokori.search.embedding.local_embed_package_available", return_value=True):
        with patch("nokori.search.embedding.local_model_cached", return_value=False):
            assert embedding.embedding_active(cfg, 0) is True
    with patch("nokori.search.embedding.local_embed_package_available", return_value=False):
        with patch("nokori.search.embedding.local_model_cached", return_value=False):
            assert embedding.embedding_active(cfg, 0) is False


def test_session_start_kickstart_spawns_when_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg2 = replace(
        cfg,
        embed_enabled=True,
        embed_base_url=None,
        embed_model=None,
        embed_server_auto_start=True,
    )
    db = open_db(cfg2.db_path)
    try:
        spawned: list[int] = []

        with patch("nokori.search.embedding.embedding_active", return_value=True):
            with patch("nokori.search.embedding.use_local_config", return_value=True):
                with patch("nokori.search.embedding.local_model_cached", return_value=True):
                    with patch(
                        "nokori.search.embedding.local_embed_package_available",
                        return_value=True,
                    ):
                        with patch("nokori.search.embed_ipc.ping", return_value=False):
                            with patch(
                                "nokori.search.embed_ipc.kickstart_server",
                                side_effect=lambda c: spawned.append(1) or False,
                            ):
                                session_start._maybe_kickstart_embed(cfg2, db)
        assert spawned == [1]
    finally:
        db.close()


def test_session_start_skips_when_weights_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        with patch("nokori.search.embedding.embedding_active", return_value=True):
            with patch("nokori.search.embedding.use_local_config", return_value=True):
                with patch("nokori.search.embedding.local_model_cached", return_value=False):
                    with patch("nokori.search.embed_ipc.kickstart_server") as ks:
                        session_start._maybe_kickstart_embed(cfg, db)
                        ks.assert_not_called()
    finally:
        db.close()


def test_install_prefetch_when_hooks_unchanged(monkeypatch, tmp_path):
    from argparse import Namespace

    from nokori.commands import install as install_cmd

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    calls: list[Config] = []

    with patch.object(install_cmd, "_read_json_file", return_value={}):
        with patch.object(install_cmd, "_merge_claude_settings", return_value={"hooks": {}}):
            with patch.object(install_cmd, "_write_json_file"):
                with patch(
                    "nokori.prefetch.maybe_prefetch_local_embed",
                    side_effect=lambda c: calls.append(c) or True,
                ):
                    rc = install_cmd.run(
                        Namespace(
                            dry_run=False,
                            uninstall=False,
                            disable=False,
                            enable=False,
                            no_prefetch_embed=False,
                        ),
                        cfg,
                    )
    assert rc == 0
    assert len(calls) == 1


def test_session_start_skips_remote_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_BASE_URL", "http://fake/v1")
    monkeypatch.setenv("NOKORI_EMBED_MODEL", "remote")
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        with patch("nokori.search.embed_ipc.kickstart_server") as ks:
            session_start._maybe_kickstart_embed(cfg, db)
            ks.assert_not_called()
    finally:
        db.close()
