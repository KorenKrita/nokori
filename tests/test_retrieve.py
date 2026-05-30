"""Tests for shared retrieval (formal + shadow pools)."""
from datetime import datetime, timezone

from nokori.config import Config
from nokori.db import open_db
from nokori.hooks.user_prompt_submit import _run_shadow_pool
from nokori.models import Rule, ScoredResult
from nokori.search import embedding as embedding_search
from nokori.search.retrieve import retrieve_and_tier


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(db, *, id_, trigger, project_id="other-proj"):
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
            "confidence, status, project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                id_, id_[:6], trigger, "action text",
                "correction", "high", "active", "project", project_id,
                now, now,
            ),
        )


def test_retrieve_and_tier_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        r = retrieve_and_tier("query", [], db, cfg)
        assert r.hot == [] and r.embed_mode == "off"
    finally:
        db.close()


def test_shadow_pool_calls_embedding_path(monkeypatch, tmp_path):
    """Shadow pool uses retrieve_and_tier (embedding when auto_enabled)."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _insert_rule(
            db,
            id_="rule-shadow",
            trigger="never git force push remote branch",
            project_id="other-proj",
        )
        embed_calls: list[int] = []

        def fake_auto(cfg, n):
            return True

        def fake_use_local(cfg):
            return False

        def fake_search(prompt, rules, db, client, top_k=10):
            embed_calls.append(1)
            return [
                ScoredResult(
                    rule=rules[0],
                    cosine=0.9,
                    bm25_score=0.0,
                    rrf_score=0.0,
                )
            ]

        monkeypatch.setattr(embedding_search, "auto_enabled", fake_auto)
        monkeypatch.setattr(embedding_search, "use_local", fake_use_local)
        monkeypatch.setattr(embedding_search, "search", fake_search)

        _run_shadow_pool(db, "git push force remote", "my-proj", cfg)
        assert embed_calls, "shadow pool should use embedding when auto_enabled"
        row = db.fetchone(
            "SELECT cross_project_hits FROM rules WHERE id = 'rule-shadow'"
        )
        assert row["cross_project_hits"] == 1
    finally:
        db.close()
