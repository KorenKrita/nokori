"""Gate marker prompt_hash validation."""

from datetime import datetime, timezone

from nokori.config import Config
from nokori.db import open_db
from nokori.gate import marker as marker_io
from nokori.gate.marker import MarkerRule, prompt_hash
from nokori.utils.host import Host


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(db, *, id_, trigger, short_id=None):
    now = _utcnow_iso()
    sid = short_id or id_.replace("-", "")[:6]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "source_origin, status, severity, "
            "project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
            (id_, sid, trigger, "use lease",
             "transcript_extraction", "active", "reminder",
             "global", None, now, now),
        )


def test_prompt_hash_matches():
    from nokori.gate.marker import Marker

    m = Marker(
        session_id="s1",
        prompt_hash="abc123",
        created_at=_utcnow_iso(),
        rules=[],
    )
    assert marker_io.prompt_hash_matches(m, "abc123") is True
    assert marker_io.prompt_hash_matches(m, "other") is False
    assert marker_io.prompt_hash_matches(m, None) is False


def test_pre_tool_use_fail_open_when_marker_only_no_injection(monkeypatch, tmp_path):
    """Orphan marker without fire events hash anchor must not block (fail-open)."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sess = "s-marker-only"
    ph = prompt_hash("git push --force the branch")
    marker_io.write(
        cfg,
        sess,
        "git push --force the branch",
        [MarkerRule("rule01", "use lease", "transcript_extraction", "rationale")],
        ph=ph,
    )
    from nokori.hooks.pre_tool_use import handle

    out = handle({"session_id": sess, "tool_name": "Bash"}, cfg, host=Host.CLAUDE)
    hso = out.get("hookSpecificOutput") or {}
    assert hso.get("permissionDecision") != "deny"
    assert not cfg.marker_path(sess, ph).exists()


def test_pre_tool_use_skips_block_on_stale_prompt_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    sess = "s-phash"
    try:
        now = _utcnow_iso()
        _insert_rule(db, id_="rule-1", trigger="git push force remote", short_id="rule01")
        ph_old = prompt_hash("git push --force the branch")
        ph_new = prompt_hash("unrelated weather question")
        marker_io.write(
            cfg,
            sess,
            "git push --force the branch",
            [MarkerRule("rule01", "use lease", "transcript_extraction", "rationale")],
            ph=ph_old,
        )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-1", "rule-1", sess, ph_new, "hot", now),
            )
        from nokori.hooks.pre_tool_use import handle

        out = handle({"session_id": sess, "tool_name": "Bash"}, cfg, host=Host.CLAUDE)
        hso = out.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") != "deny"
        assert not cfg.marker_path(sess, ph_old).exists()
    finally:
        db.close()


def test_per_prompt_hash_markers_do_not_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sess = "s-multi"
    ph_a = prompt_hash("prompt A about deploy")
    ph_b = prompt_hash("prompt B about tests")

    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        _insert_rule(db, id_="rule-a", trigger="deploy trigger", short_id="aaaaaa")
        _insert_rule(db, id_="rule-b", trigger="test trigger", short_id="bbbbbb")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-a", "rule-a", sess, ph_a, "hot", now),
            )
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-b", "rule-b", sess, ph_b, "hot", now),
            )

        marker_io.write(
            cfg, sess, "prompt A about deploy",
            [MarkerRule("aaaaaa", "action a")], ph=ph_a,
        )
        marker_io.write(
            cfg, sess, "prompt B about tests",
            [MarkerRule("bbbbbb", "action b")], ph=ph_b,
        )
        assert cfg.marker_path(sess, ph_a).exists()
        assert cfg.marker_path(sess, ph_b).exists()
        from nokori.hooks.pre_tool_use import handle

        out = handle(
            {"session_id": sess, "tool_name": "Bash", "prompt": "prompt A about deploy"},
            cfg,
            host=Host.CLAUDE,
        )
        assert (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
        assert not cfg.marker_path(sess, ph_a).exists()
        assert cfg.marker_path(sess, ph_b).exists()
    finally:
        db.close()
