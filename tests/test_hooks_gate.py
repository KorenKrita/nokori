import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

def _set_rule_lifecycle(
    tmp_path,
    short_id: str,
    *,
    status: str,
    severity: str | None = None,
    observed: bool = False,
):
    from nokori.db import open_db

    db = open_db(tmp_path / "rules.db")
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        updates = ["status = ?", "updated_at = ?"]
        params = [status, now]
        if severity is not None:
            updates.append("severity = ?")
            params.append(severity)
        if observed:
            updates.append("first_observed_useful_at = ?")
            params.append(now)
        params.append(short_id)
        with db.transaction() as tx:
            cur = tx.execute(
                f"UPDATE rules SET {', '.join(updates)} WHERE short_id = ?",
                tuple(params),
            )
            assert cur.rowcount == 1
    finally:
        db.close()


def _run(*args, env_extra=None, stdin: str = ""):
    env = {"PATH": "/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def _gate_denied(out: dict) -> bool:
    hso = out.get("hookSpecificOutput") or {}
    return hso.get("permissionDecision") == "deny"


def _gate_deny_reason(out: dict) -> str:
    hso = out.get("hookSpecificOutput") or {}
    return hso.get("permissionDecisionReason") or ""


def _add_rule(
    tmp_path,
    trigger,
    action,
    *,
    severity: str = "high_risk",
    variants=None,
):
    args = [
        "--trigger", trigger,
        "--action", action,
        "--severity", severity,
    ]
    if variants:
        args.extend(["--variants", ",".join(variants)])
    r = _run("add", *args, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    return r.stdout.split()[1]


def _add_high_active(tmp_path, trigger, action, variants=None):
    short = _add_rule(tmp_path, trigger, action, variants=variants)
    # v6 lifecycle promotion is autonomous; tests seed trusted fixtures directly.
    _set_rule_lifecycle(
        tmp_path,
        short,
        status="trusted",
        severity="gate_eligible",
        observed=True,
    )
    return short


def test_user_prompt_injects(tmp_path):
    short = _add_high_active(
        tmp_path,
        "Never force push to a shared branch",
        "use --force-with-lease",
        variants=["git push --force"],
    )
    payload = json.dumps({
        "session_id": "s-test-1",
        "cwd": str(tmp_path),
        "prompt": "ok let me git push --force this fix",
    })
    r = _run("hook", "user-prompt-submit",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path)}, stdin=payload)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    text = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "[Nokori]" in text
    assert short in text


def test_pre_tool_use_blocks_when_marker_present(tmp_path):
    short = _add_high_active(tmp_path, "force push to main", "use lease",
                             variants=["git push --force"])
    sess = "s-test-2"
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": "git push --force the branch"}))
    r = _run("hook", "pre-tool-use",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
             stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}))
    out = json.loads(r.stdout)
    assert _gate_denied(out)
    assert short in _gate_deny_reason(out)
    assert "decision" not in out


def test_gate_marker_keeps_decision_features(tmp_path):
    from nokori.config import Config
    from nokori.gate import marker as marker_io

    short = _add_high_active(tmp_path, "force push to main", "use lease",
                             variants=["git push --force"])
    sess = "s-test-marker-features"
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": "git push --force the branch"}))
    cfg = replace(Config.from_env(), data_dir=tmp_path)
    marker = marker_io.read_latest_marker(cfg, sess)

    assert marker is not None
    assert marker.rules
    rule = next(r for r in marker.rules if r.short_id == short)
    assert rule.decision_features is not None
    assert rule.decision_features["required_concepts_match"] is True


def test_pre_tool_use_passes_when_no_marker(tmp_path):
    r = _run("hook", "pre-tool-use",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
             stdin=json.dumps({"session_id": "no-marker", "tool_name": "Bash"}))
    out = json.loads(r.stdout)
    assert not _gate_denied(out)
    assert out == {} or out.get("continue") is True


def test_marker_consumed_once(tmp_path):
    _add_high_active(tmp_path, "force push", "lease",
                     variants=["git push --force"])
    sess = "s-test-3"
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": "git push --force"}))
    r1 = _run("hook", "pre-tool-use",
              env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
              stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}))
    assert _gate_denied(json.loads(r1.stdout))
    r2 = _run("hook", "pre-tool-use",
              env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
              stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}))
    assert not _gate_denied(json.loads(r2.stdout))


def test_marker_ttl_zero_never_expires(tmp_path):
    """gate_ttl_seconds=0 means no expiry (issue #96)."""
    _add_high_active(tmp_path, "force push", "lease",
                     variants=["git push --force"])
    sess = "s-test-4"
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path),
                    "NOKORI_GATE_TTL_SECONDS": "0"},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": "git push --force"}))
    time.sleep(1.1)
    r = _run("hook", "pre-tool-use",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path),
                        "NOKORI_GATE_TTL_SECONDS": "0"},
             stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}))
    assert _gate_denied(json.loads(r.stdout))


def test_dismiss_cli(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from nokori.config import Config
    from nokori.db import open_db

    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    short = _add_high_active(tmp_path, "rule x", "do y")
    env = {"NOKORI_DATA_DIR": str(tmp_path)}
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z",
        )
        row = db.fetchone("SELECT id FROM rules WHERE short_id = ?", (short,))
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-dismiss", row["id"], "sess-dismiss-cli", "ph1", "hot", now),
            )
    finally:
        db.close()
    r = _run("dismiss", short, env_extra=env)
    assert r.returncode == 0, r.stderr
    show = _run("show", short, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert "archived" in show.stdout


def test_dismiss_via_prompt_archives_recent_injected(tmp_path):
    short = _add_high_active(tmp_path, "force push", "lease",
                             variants=["git push --force"])
    sess = "s-dismiss"
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": "git push --force"}))
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": f"this rule is outdated, dismiss {short}"}))
    show = _run("show", short, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert "archived" in show.stdout


def test_disabled_short_circuits_user_prompt(tmp_path):
    _add_high_active(tmp_path, "force push", "lease", variants=["git push --force"])
    r = _run("hook", "user-prompt-submit",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path), "NOKORI_DISABLED": "1"},
             stdin=json.dumps({"session_id": "x", "prompt": "git push --force"}))
    assert json.loads(r.stdout.strip()) == {"continue": True}


def test_hook_without_cwd_injects_global_rules_only(tmp_path):
    """No cwd → project_id unresolved; must not inject other projects' rules."""
    env = {"NOKORI_DATA_DIR": str(tmp_path), "NOKORI_GATE_ENABLED": "0"}
    proj_short = _run(
        "add",
        "--trigger",
        "project only force push branch",
        "--action",
        "use lease in this repo",
        "--variants",
        "git push --force proj",
        "--project-id",
        "proj-isolated",
        env_extra=env,
    ).stdout.split()[1]
    global_short = _run(
        "add",
        "--trigger",
        "global never force push shared branch",
        "--action",
        "use --force-with-lease globally",
        "--variants",
        "git push --force",
        env_extra=env,
    ).stdout.split()[1]
    _set_rule_lifecycle(
        tmp_path,
        proj_short,
        status="active",
        severity="reminder",
        observed=True,
    )
    _set_rule_lifecycle(
        tmp_path,
        global_short,
        status="active",
        severity="reminder",
        observed=True,
    )

    r = _run(
        "hook",
        "user-prompt-submit",
        env_extra=env,
        stdin=json.dumps({
            "session_id": "s-no-cwd",
            "prompt": "ok let me git push --force to remote",
        }),
    )
    assert r.returncode == 0, r.stderr
    text = json.loads(r.stdout).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert global_short in text
    assert proj_short not in text


def test_solution_hot_injects_without_gate(monkeypatch, tmp_path):
    """solution may HOT-inject but must not write a gate marker or block tools."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    short = _add_rule(
        tmp_path,
        "Never force push to a shared branch",
        "use --force-with-lease",
        severity="reminder",
        variants=["git push --force"],
    )
    env_extra = {"NOKORI_DATA_DIR": str(tmp_path)}
    _set_rule_lifecycle(
        tmp_path,
        short,
        status="active",
        severity="reminder",
        observed=True,
    )
    sess = "s-solution-no-gate"
    r = _run(
        "hook",
        "user-prompt-submit",
        env_extra=env_extra,
        stdin=json.dumps({
            "session_id": sess,
            "cwd": str(tmp_path),
            "prompt": "ok let me git push --force this fix",
        }),
    )
    assert r.returncode == 0, r.stderr
    text = json.loads(r.stdout).get("hookSpecificOutput", {}).get(
        "additionalContext", ""
    )
    assert short in text

    from nokori.config import Config
    from nokori.gate import marker as marker_io

    cfg = Config.from_env()
    assert marker_io.read(cfg, sess) is None

    r2 = _run(
        "hook",
        "pre-tool-use",
        env_extra=env_extra,
        stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}),
    )
    assert not _gate_denied(json.loads(r2.stdout))


def test_pre_tool_use_skips_non_matching_tool(tmp_path):
    """PreToolUse should not block tools not in gate_matcher."""
    _add_high_active(tmp_path, "force push", "lease", variants=["git push --force"])
    sess = "s-read-tool"
    _run("hook", "user-prompt-submit",
         env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
         stdin=json.dumps({"session_id": sess, "cwd": str(tmp_path),
                           "prompt": "git push --force"}))
    # Read tool is NOT in the default gate_matcher
    r = _run("hook", "pre-tool-use",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
             stdin=json.dumps({"session_id": sess, "tool_name": "Read"}))
    out = json.loads(r.stdout)
    assert not _gate_denied(out)
