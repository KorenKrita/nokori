import json
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest


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


def _add_high_active(tmp_path, trigger, action, variants=None):
    args = [
        "--trigger", trigger,
        "--action", action,
        "--source-type", "correction",
        "--confidence", "high",
    ]
    if variants:
        args.extend(["--variants", ",".join(variants)])
    r = _run("add", *args, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    return r.stdout.split()[1]


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
    assert out.get("decision") == "block"
    assert short in out.get("reason", "")


def test_pre_tool_use_passes_when_no_marker(tmp_path):
    r = _run("hook", "pre-tool-use",
             env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
             stdin=json.dumps({"session_id": "no-marker", "tool_name": "Bash"}))
    out = json.loads(r.stdout)
    assert "decision" not in out


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
    assert json.loads(r1.stdout).get("decision") == "block"
    r2 = _run("hook", "pre-tool-use",
              env_extra={"NOKORI_DATA_DIR": str(tmp_path)},
              stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}))
    assert "decision" not in json.loads(r2.stdout)


def test_marker_expires(tmp_path):
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
    assert "decision" not in json.loads(r.stdout)


def test_dismiss_cli(tmp_path):
    short = _add_high_active(tmp_path, "rule x", "do y")
    r = _run("dismiss", short, env_extra={"NOKORI_DATA_DIR": str(tmp_path)})
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
    assert r.stdout.strip() == "{}"
