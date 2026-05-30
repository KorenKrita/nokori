import pytest

from nokori.search import bm25


@pytest.fixture(autouse=True)
def _clear_bm25_index_cache():
    bm25.clear_index_cache()
    yield
    bm25.clear_index_cache()
