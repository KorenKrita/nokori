import pytest

from nokori.search import bm25


@pytest.fixture(autouse=True)
def _clear_bm25_index_cache():
    bm25._INDEX_CACHE.clear()
    yield
    bm25._INDEX_CACHE.clear()


@pytest.fixture(autouse=True)
def _disable_embed_and_network_downloads(monkeypatch):
    """Prevent tests from enabling embedding or downloading models."""
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "0")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
