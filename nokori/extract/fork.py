"""Fork-based extraction: reuse Claude Code's prompt cache for the extractor role.

Only applicable to Claude Code sessions (not Cursor). Forks the ended session
with --no-session-persistence, passing the extraction prompt as a user message
so the conversation prefix remains identical and the prompt cache is hit.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from ..config import Config
from ..utils.logging import get_logger

log = get_logger("nokori.extract.fork")

_FORK_TIMEOUT_SECONDS = 300


_FORK_TASK_PREAMBLE = """\
[New task] The conversation above is the transcript to extract from. \
Perform the extraction task described below and output ONLY the JSON result.

"""


def fork_extract(session_id: str, extract_prompt: str, cfg: Config) -> str | None:
    """Attempt extraction via session fork. Returns raw JSON string or None on failure."""
    if not _claude_cli_available():
        log.info("claude CLI not found, fork extract unavailable")
        return None

    if not _valid_session_id(session_id):
        log.warning("fork extract: invalid session_id format: %s", session_id[:60])
        return None

    prompt = _FORK_TASK_PREAMBLE + extract_prompt

    env = _build_env(cfg)
    cmd = [
        "claude", "-r", session_id,
        "--fork-session",
        "--no-session-persistence",
        "--max-turns", "1",
        "--tools", "",
        "-p", prompt,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FORK_TIMEOUT_SECONDS,
            env=env,
        )
    except FileNotFoundError:
        log.warning("claude CLI not found during fork extract")
        return None
    except subprocess.TimeoutExpired:
        log.warning("fork extract timed out after %ds", _FORK_TIMEOUT_SECONDS)
        return None

    if result.returncode != 0:
        stderr_preview = (result.stderr or "")[:200]
        log.warning("fork extract exited %d: %s", result.returncode, stderr_preview)
        return None

    output = result.stdout.strip()
    if not output:
        log.warning("fork extract returned empty output")
        return None

    from ..llm.json_payload import parse_json_payload

    try:
        json.loads(output)
        return output
    except (json.JSONDecodeError, ValueError):
        pass

    parsed = parse_json_payload(output)
    if isinstance(parsed, dict) and "candidates" in parsed:
        return json.dumps(parsed, ensure_ascii=False)

    if parsed is not None:
        log.warning("fork extract parsed JSON but missing 'candidates' key: %s", str(parsed)[:200])
    else:
        log.warning("fork extract output is not valid JSON: %s", output[:200])
    return None


_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def _valid_session_id(session_id: str) -> bool:
    """Reject session IDs with unsafe characters or CLI flag patterns."""
    if not session_id or session_id.startswith("-"):
        return False
    return bool(_SESSION_ID_RE.match(session_id))


def _claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def _build_env(cfg: Config) -> dict[str, str]:
    """Build env for the forked claude process.

    Inherits full environment so claude CLI retains all user configuration
    (API endpoint, model, proxy, caching flags, etc.). Only overrides vars
    needed to prevent hook recursion.
    """
    env = os.environ.copy()
    env["NOKORI_EXTRACTING"] = "1"
    env["NOKORI_DATA_DIR"] = str(cfg.data_dir)
    return env
