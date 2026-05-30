import json
import subprocess
import sys


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


def test_stale_marker_cleared_when_no_gate_rules(tmp_path):
    """Prompt A sets marker; prompt B with no match clears marker before tools run."""
    _add_high_active(
        tmp_path,
        "Never force push to a shared branch",
        "use --force-with-lease",
        variants=["git push --force"],
    )
    env = {"NOKORI_DATA_DIR": str(tmp_path)}
    sess = "stale-marker"
    _run(
        "hook",
        "user-prompt-submit",
        env_extra=env,
        stdin=json.dumps({
            "session_id": sess,
            "cwd": str(tmp_path),
            "prompt": "git push --force the branch",
        }),
    )
    _run(
        "hook",
        "user-prompt-submit",
        env_extra=env,
        stdin=json.dumps({
            "session_id": sess,
            "cwd": str(tmp_path),
            "prompt": "what is the weather today",
        }),
    )
    r = _run(
        "hook",
        "pre-tool-use",
        env_extra=env,
        stdin=json.dumps({"session_id": sess, "tool_name": "Bash"}),
    )
    out = json.loads(r.stdout)
    assert "decision" not in out
