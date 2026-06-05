"""Code-review round: regression tests for reported issues."""

import json


from nokori.config import Config
from nokori.db import open_db, log_injections_batch
from nokori.extract.merger import MergeOutcome, merge_candidate
from nokori.models import Rule
from nokori.search import bm25
from nokori.utils.time import now_iso


class FakeMergeLLM:
    def __init__(self, response: str):
        self.response = response

    def complete_messages(self, system, user, **kwargs):
        return self.response


def _cand(trigger: str, action: str):
    from nokori.extract.extractor import Candidate

    return Candidate(
        trigger=trigger,
        trigger_variants=[],
        search_terms={},
        behavior=None,
        action=action,
        rationale=None,
    )


def test_merge_null_relationships_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(Config.from_env().db_path)
    try:
        merge_candidate(
            _cand("git push force shared", "action one"),
            db,
            FakeMergeLLM("[]"),
        )
        out = merge_candidate(
            _cand("git push force shared branch", "action two"),
            db,
            FakeMergeLLM(json.dumps({"relationships": None})),
        )
        assert isinstance(out, MergeOutcome)
    finally:
        db.close()


def test_bm25_index_cache_ignores_updated_at(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    bm25.clear_index_cache()
    now = now_iso()
    rule = Rule(
        id="r1",
        short_id="abc123",
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="v1",
        runtime_policy_version="v1",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical="git push force",
        trigger_variants=["force push"],
        search_terms={},
        action_instruction="use lease",
        project_scope="global",
        project_id=None,
        archived_reason=None,
        created_at=now,
        updated_at=now,
    )
    bm25.search("git push", [rule])
    assert len(bm25._INDEX_CACHE) == 1
    from dataclasses import replace as dreplace
    bumped = dreplace(rule, updated_at="2099-01-01T00:00:00Z")
    bm25.search("git push", [bumped])
    assert len(bm25._INDEX_CACHE) == 1


def test_export_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = now_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?)",
                (
                    "id1", "abcd12", "t", "a", "active", "reminder",
                    "global", None, now, now,
                ),
            )
        target = tmp_path / "out.json"
        from nokori.commands import export_import

        export_import.run_export(
            __import__("argparse").Namespace(path=str(target)),
            cfg,
        )
        assert target.exists()
        assert not target.with_suffix(target.suffix + ".tmp").exists()
    finally:
        db.close()


def test_hot_injection_creates_fire_event(monkeypatch, tmp_path):
    """log_injections_batch creates fire events (replaced old hit_count logic)."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(Config.from_env().db_path)
    try:
        now = now_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?)",
                (
                    "id1", "abcd12", "t", "a", "active", "reminder",
                    "global", None, now, now,
                ),
            )
        log_injections_batch(db, "sess", "phash", [("id1", "hot")], now)
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM rule_fire_events WHERE rule_id = 'id1'"
        )
        assert row["n"] >= 1
    finally:
        db.close()


def test_corrupt_extract_job_quarantined(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    bad = cfg.jobs_dir / "extract-deadbeef.json"
    bad.write_text("{not json", encoding="utf-8")
    from nokori.extract import jobs as job_io

    pending = job_io.list_jobs(cfg)
    assert bad not in pending
    assert not bad.exists()
    assert (cfg.jobs_dir / "bad" / "extract-deadbeef.json").exists()


def test_active_rule_retrieval_in_same_session(monkeypatch, tmp_path):
    """An active rule retrieves correctly when prompted in the same session."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = now_iso()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                "created_by_pipeline_version, runtime_policy_version, "
                "trigger_canonical, action_instruction, "
                "status, severity, project_scope, project_id, "
                "created_at, updated_at) "
                "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?)",
                (
                    "d1", "dorm01", "git push force remote",
                    "use lease", "active", "reminder",
                    "global", None, now, now,
                ),
            )
        from nokori.hooks.user_prompt_submit import handle
        from nokori.utils.host import Host

        handle({
            "session_id": "s-test",
            "prompt": "git push force remote branch please",
            "cwd": str(tmp_path),
        }, cfg, host=Host.CLAUDE)
        row = db.fetchone("SELECT status FROM rules WHERE id = 'd1'")
        assert row["status"] == "active"
    finally:
        db.close()
