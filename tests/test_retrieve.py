"""Tests for shared retrieval (formal + shadow pools)."""
from datetime import datetime, timezone

from nokori.config import Config
from nokori.db import open_db
from nokori.db import fetch_rules, fetch_shadow_rules, total_rule_count
from nokori.models import ScoredResult
from nokori.search import embedding as embedding_search
from nokori.search.retrieve import retrieve_and_tier, retrieve_formal_and_shadow


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(db, *, id_, trigger, project_id="other-proj", short_id=None):
    now = _utcnow_iso()
    sid = short_id or id_.replace("-", "")[:6]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
            "confidence, status, project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                id_, sid, trigger, "action text",
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


def test_retrieve_formal_and_shadow_splits_pools(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _insert_rule(
            db,
            id_="rule-formal",
            trigger="never git force push shared branch",
            project_id="my-proj",
            short_id="form01",
        )
        _insert_rule(
            db,
            id_="rule-shadow",
            trigger="never git force push remote branch",
            project_id="other-proj",
            short_id="shad01",
        )
        formal_rules = fetch_rules(
            db, statuses=("active",), project_id="my-proj"
        )
        shadow_rules = fetch_shadow_rules(db, project_id="my-proj")
        result, shadow_hot, _shadow_warm = retrieve_formal_and_shadow(
            "git push --force to remote",
            formal_rules,
            shadow_rules,
            db,
            cfg,
        )
        formal_ids = {r.id for r in formal_rules}
        assert all(r.rule.id in formal_ids for r in result.hot + result.warm)
        if shadow_hot:
            assert shadow_hot[0].rule.id == "rule-shadow"
    finally:
        db.close()


def test_hook_uses_shared_local_embed(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        _insert_rule(db, id_="rule-local", trigger="deploy production database")
        from nokori.db import RULE_COLUMNS, row_to_rule

        row = db.fetchone(f"SELECT {RULE_COLUMNS} FROM rules WHERE id = 'rule-local'")
        rule = row_to_rule(row)
        shared_calls: list[int] = []

        def fake_shared(query, rules, db, cfg, **kwargs):
            shared_calls.append(1)
            return [], "local"

        monkeypatch.setattr(embedding_search, "auto_enabled", lambda c, n: True)
        monkeypatch.setattr(embedding_search, "use_local", lambda c: True)
        monkeypatch.setattr(embedding_search, "search_local_shared", fake_shared)

        r = retrieve_and_tier("deploy db", [rule], db, cfg, interaction="hook")
        assert shared_calls
        assert r.embed_mode == "local"
    finally:
        db.close()


def test_auto_enabled_uses_retrieval_pool_size(monkeypatch, tmp_path):
    """Embedding threshold uses len(rules) for this query, not global DB count."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        for i in range(20):
            _insert_rule(
                db,
                id_=f"aaaaaa{i:02d}-0000-4000-8000-000000000000",
                short_id=f"a{i:05x}",
                trigger=f"trigger number {i} unique tokens",
                project_id="proj-a",
            )
        now = _utcnow_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
                "confidence, status, project_scope, project_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "global-0000-4000-8000-000000000001",
                    "glob01",
                    "global rule trigger tokens",
                    "act",
                    "correction",
                    "high",
                    "active",
                    "global",
                    None,
                    now,
                    now,
                ),
            )
        from nokori.db import fetch_rules, total_rule_count

        assert total_rule_count(db) == 21
        pool = fetch_rules(db, statuses=("active",), project_id="proj-b")
        assert len(pool) == 1

        checked: list[int] = []

        def fake_auto(c, n):
            checked.append(n)
            return False

        monkeypatch.setattr(embedding_search, "auto_enabled", fake_auto)
        retrieve_and_tier("global rule", pool, db, cfg, interaction="cli")
        assert checked == [1]
    finally:
        db.close()


def test_formal_shadow_pool_size_for_embed(monkeypatch, tmp_path):
    """Combined formal+shadow len gates embed, not global DB size."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        for i in range(25):
            _insert_rule(
                db,
                id_=f"other-{i:02d}",
                trigger=f"other project rule {i}",
                project_id="other-proj",
                short_id=f"oth{i:04x}",
            )
        _insert_rule(
            db,
            id_="local-01",
            trigger="only local rule here",
            project_id="my-proj",
            short_id="loc001",
        )
        formal = fetch_rules(db, statuses=("active", "dormant"), project_id="my-proj")
        shadow = fetch_shadow_rules(db, project_id="my-proj")
        checked: list[int] = []

        def fake_auto(c, n):
            checked.append(n)
            return False

        monkeypatch.setattr(embedding_search, "auto_enabled", fake_auto)
        retrieve_formal_and_shadow(
            "only local",
            formal,
            shadow,
            db,
            cfg,
            interaction="hook",
        )
        assert checked == [len(formal) + len(shadow)]
        assert checked[0] < 999
    finally:
        db.close()
