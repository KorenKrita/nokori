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


def test_embed_chunk_params_local_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.embed_chunk_size == 4000
    assert cfg.embed_chunk_count == 2
    assert cfg.embed_chunk_size_configured is False
    assert cfg.embed_chunk_count_configured is False
    size, count = embedding.embed_chunk_params(cfg, local=True)
    assert size == embedding.LOCAL_EMBED_CHUNK_SIZE
    assert count == embedding.LOCAL_EMBED_CHUNK_COUNT
    rsize, rcount = embedding.embed_chunk_params(cfg, local=False)
    assert rsize == 4000 and rcount == 2


def test_embed_chunk_params_partial_size_only(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_CHUNK_SIZE", "1024")
    cfg = Config.from_env()
    assert cfg.embed_chunk_size_configured is True
    assert cfg.embed_chunk_count_configured is False
    size, count = embedding.embed_chunk_params(cfg, local=True)
    assert size == 1024
    assert count == embedding.LOCAL_EMBED_CHUNK_COUNT


def test_embed_chunk_params_empty_env_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_CHUNK_SIZE", "")
    cfg = Config.from_env()
    assert cfg.embed_chunk_size_configured is False
    assert cfg.embed_chunk_size == 4000
    size, count = embedding.embed_chunk_params(cfg, local=True)
    assert size == embedding.LOCAL_EMBED_CHUNK_SIZE
    assert count == embedding.LOCAL_EMBED_CHUNK_COUNT


def test_local_single_chunk_for_long_rule_text():
    text = "x" * 5000
    chunks = embedding._chunk_text(
        text,
        chunk_size=embedding.LOCAL_EMBED_CHUNK_SIZE,
        chunk_count=embedding.LOCAL_EMBED_CHUNK_COUNT,
    )
    assert chunks == [text]


def test_local_model_hub_dir():
    assert embedding.local_model_hub_dir(
        "ibm-granite/granite-embedding-97m-multilingual-r2"
    ) == "models--ibm-granite--granite-embedding-97m-multilingual-r2"
    assert embedding.local_model_hub_dir("foo-bar") == (
        "models--sentence-transformers--foo-bar"
    )


def test_encode_chunks_uses_query_and_document_api():
    from unittest.mock import MagicMock

    model = MagicMock()
    arr = MagicMock()
    arr.tolist.return_value = [0.0, 1.0]
    model.encode_query.return_value = [arr]
    model.encode_document.return_value = [arr]

    embedding._encode_chunks(model, ["q"], kind="query")
    model.encode_query.assert_called_once()
    model.encode_document.assert_not_called()

    model.encode_query.reset_mock()
    embedding._encode_chunks(model, ["d"], kind="document")
    model.encode_document.assert_called_once()


def test_search_skips_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        client = embedding.EmbeddingClient(cfg)
        assert embedding.search("anything", [], db, client) == []
    finally:
        db.close()
