"""Gate marker prompt_hash validation."""

import json
from datetime import UTC, datetime

from nokori.config import Config
from nokori.db import open_db
from nokori.gate import marker as marker_io
from nokori.gate.marker import MarkerRule, prompt_hash
from nokori.policy import RUNTIME_POLICY_VERSION
from nokori.utils.host import Host


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_rule(
    db,
    *,
    id_,
    trigger,
    short_id=None,
    status="active",
    severity="reminder",
    excluded_contexts=None,
):
    now = _utcnow_iso()
    sid = short_id or id_.replace("-", "")[:6]
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, excluded_contexts, "
            "source_origin, status, severity, "
            "project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,1,1,'v1',?,?,?,?,?,?,?,?,?,?,?)",
            (id_, sid, RUNTIME_POLICY_VERSION,
             trigger, "use lease", json.dumps(excluded_contexts or []),
             "transcript_extraction", status, severity,
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
        [MarkerRule("rule01", "use lease", trigger="deploy command", rationale="rationale")],
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
            [MarkerRule("rule01", "use lease", trigger="git push force remote", rationale="rationale")],
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


def test_pre_tool_use_rechecks_tool_input_only_regex_exclusion(monkeypatch, tmp_path):
    """Gate revalidation must use excluded_context match_mode, not substring only."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    sess = "s-tool-exclusion"
    ph = prompt_hash("run deploy command")
    try:
        now = _utcnow_iso()
        _insert_rule(
            db,
            id_="rule-regex",
            short_id="regex1",
            trigger="deploy command",
            status="trusted",
            severity="gate_eligible",
            excluded_contexts=[
                {
                    "id": "fixture-tool-input",
                    "patterns": [r"sandbox-\d+"],
                    "scope": "tool_input_only",
                    "match_mode": "regex",
                }
            ],
        )
        marker_io.write(
            cfg,
            sess,
            "run deploy command",
            [MarkerRule("regex1", "use lease", trigger="deploy command", rationale="rationale")],
            ph=ph,
        )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-regex", "rule-regex", sess, ph, "hot", now),
            )
        from nokori.hooks.pre_tool_use import handle

        out = handle(
            {
                "session_id": sess,
                "tool_name": "Bash",
                "tool_input": {"command": "deploy command in sandbox-42"},
            },
            cfg,
            host=Host.CLAUDE,
        )

        hso = out.get("hookSpecificOutput") or {}
        assert hso.get("permissionDecision") != "deny"
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
        _insert_rule(
            db,
            id_="rule-a",
            trigger="deploy trigger",
            short_id="aaaaaa",
            status="trusted",
            severity="gate_eligible",
        )
        _insert_rule(
            db,
            id_="rule-b",
            trigger="test trigger",
            short_id="bbbbbb",
            status="trusted",
            severity="gate_eligible",
        )
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


def test_pre_tool_use_revalidates_marker_rule_lifecycle(
    monkeypatch, tmp_path
):
    """A marker cannot Gate unless the DB row is currently trusted+gate_eligible."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sess = "s-marker-lifecycle"
    ph = prompt_hash("deploy trigger")
    db = open_db(cfg.db_path)
    try:
        now = _utcnow_iso()
        _insert_rule(
            db,
            id_="rule-not-gate",
            trigger="deploy trigger",
            short_id="nogate",
            status="active",
            severity="reminder",
        )
        marker_io.write(
            cfg,
            sess,
            "deploy trigger",
            [MarkerRule("nogate", "use lease", trigger="deploy trigger")],
            ph=ph,
        )
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events "
                "(id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-not-gate", "rule-not-gate", sess, ph, "hot", now),
            )
        from nokori.hooks.pre_tool_use import handle

        out = handle(
            {"session_id": sess, "tool_name": "Bash", "prompt": "deploy trigger"},
            cfg,
            host=Host.CLAUDE,
        )
        assert (out.get("hookSpecificOutput") or {}).get("permissionDecision") != "deny"
    finally:
        db.close()
