import json
import struct
from unittest.mock import patch

import pytest

from nokori.config import Config
from nokori.db import open_db
from nokori.errors import EmbeddingError
from nokori.search import embedding


def _cfg(monkeypatch, tmp_path, **overrides):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_BASE_URL", "http://fake")
    monkeypatch.setenv("NOKORI_EMBED_MODEL", "fake-embed")
    for k, v in overrides.items():
        monkeypatch.setenv(k, str(v))
    return Config.from_env()


def test_serialize_roundtrip():
    raw = embedding._serialize([0.1, 0.2, -0.3])
    assert embedding._deserialize(raw) == pytest.approx([0.1, 0.2, -0.3])


def test_cosine_basic():
    assert embedding._cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert embedding._cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert embedding._cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_chunk_short_text_one_chunk():
    assert embedding._chunk_text("hi", 100, 3) == ["hi"]


def test_chunk_long_text_splits():
    text = "abc " * 200
    chunks = embedding._chunk_text(text, 100, 3)
    assert 1 < len(chunks) <= 3


def test_auto_enabled_thresholds(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, NOKORI_EMBED_ENABLED="0")
    assert embedding.auto_enabled(cfg, 5) is False
    assert embedding.auto_enabled(cfg, 25) is True


def test_auto_enabled_explicit(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, NOKORI_EMBED_ENABLED="1")
    assert embedding.auto_enabled(cfg, 0) is True


def test_search_skips_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        client = embedding.EmbeddingClient(cfg)
        assert embedding.search("anything", [], db, client) == []
    finally:
        db.close()
