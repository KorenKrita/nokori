
import pytest

from nokori.config import Config
from nokori.db import open_db
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


def _seed_rules(db, now):
    """Insert 5 active + 18 candidate rules for pool count tests."""
    with db.transaction() as tx:
        for i in range(5):
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "runtime_policy_version, status, severity, trigger_canonical, "
                "action_instruction, concepts, required_concept_groups, "
                "trigger_variants, search_terms, source_origin, "
                "project_scope, created_at, updated_at) "
                "VALUES (?, ?, 7, 1, '1.0.0', 'active', 'reminder', ?, ?, "
                "'[]', '[]', '[]', '{}', 'transcript_extraction', 'global', ?, ?)",
                (f"rule-active-{i}", f"ra{i:04d}", f"trigger {i}", f"action {i}", now, now),
            )
        for i in range(18):
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "runtime_policy_version, status, severity, trigger_canonical, "
                "action_instruction, concepts, required_concept_groups, "
                "trigger_variants, search_terms, source_origin, "
                "project_scope, created_at, updated_at) "
                "VALUES (?, ?, 7, 1, '1.0.0', 'candidate', 'reminder', ?, ?, "
                "'[]', '[]', '[]', '{}', 'transcript_extraction', 'global', ?, ?)",
                (f"rule-cand-{i}", f"rc{i:04d}", f"trigger cand {i}", f"action cand {i}", now, now),
            )


def _make_test_rule():
    from nokori.models import Rule

    return Rule(
        id="rule-active-0",
        short_id="ra0000",
        schema_version=7,
        rule_version=1,
        created_by_pipeline_version=None,
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical="trigger 0",
        action_instruction="action 0",
    )


def test_index_rule_if_enabled_uses_retrieval_pool_when_promotion_enabled(monkeypatch, tmp_path):
    """When promotion_enabled=True and candidate rules push pool >= 20 but active+trusted < 20,
    index_rule_if_enabled should still proceed (not short-circuit)."""
    from unittest.mock import patch

    from nokori.db import retrieval_pool_count, total_rule_count
    from nokori.utils.time import now_iso

    cfg = _cfg(monkeypatch, tmp_path, NOKORI_PROMOTION_ENABLED="1")
    db = open_db(cfg.db_path)
    now = now_iso()
    try:
        _seed_rules(db, now)

        assert total_rule_count(db) == 5
        assert retrieval_pool_count(db) == 23

        with patch("nokori.search.embedding.store_rule_embedding") as mock_store:
            embedding.index_rule_if_enabled(db, _make_test_rule(), cfg)
            assert mock_store.called, (
                "index_rule_if_enabled should proceed when retrieval_pool_count >= 20 "
                "even though total_rule_count < 20"
            )

    finally:
        db.close()


def test_index_rule_if_enabled_uses_total_count_when_promotion_disabled(monkeypatch, tmp_path):
    """When promotion_enabled=False, index_rule_if_enabled uses total_rule_count (active+trusted).
    With only 5 active rules and 18 candidates, it should NOT proceed."""
    from unittest.mock import patch

    from nokori.db import retrieval_pool_count, total_rule_count
    from nokori.utils.time import now_iso

    cfg = _cfg(monkeypatch, tmp_path, NOKORI_PROMOTION_ENABLED="0")
    db = open_db(cfg.db_path)
    now = now_iso()
    try:
        _seed_rules(db, now)

        assert total_rule_count(db) == 5
        assert retrieval_pool_count(db) == 23

        with patch("nokori.search.embedding.store_rule_embedding") as mock_store:
            embedding.index_rule_if_enabled(db, _make_test_rule(), cfg)
            assert not mock_store.called, (
                "index_rule_if_enabled should NOT proceed when promotion_enabled=False "
                "and total_rule_count < 20"
            )

    finally:
        db.close()
