from __future__ import annotations

import json
import sys

from ..config import Config
from ..utils.host import detect_host_from_payload
from ..utils.hook_diag import log_hook_enter, log_hook_exit
from ..utils.logging import get_logger
from .coalesce import claim_key_for_event, duplicate_passthrough, try_claim

_log = get_logger("nokori.hooks")


def dispatch(event: str, cfg: Config) -> int:
    """Hook event dispatcher. Reads stdin JSON, writes stdout JSON."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        _log.warning("hook stdin JSON invalid; using empty payload")
        payload = {}

    host = detect_host_from_payload(payload)
    log_hook_enter(
        _log, cli_event=event, payload=payload, raw_stdin_len=len(raw), host=host,
    )

    claim_key = claim_key_for_event(event, payload)
    if claim_key and not try_claim(cfg, claim_key, cli_event=event):
        response = duplicate_passthrough(event, host)
        log_hook_exit(_log, cli_event=event, host=host, response=response)
        print(json.dumps(response, ensure_ascii=False))
        return 0

    try:
        if event == "session-start":
            from . import session_start

            response = session_start.handle(payload, cfg, host=host)
        elif event == "user-prompt-submit":
            from . import user_prompt_submit

            response = user_prompt_submit.handle(payload, cfg, host=host)
        elif event == "pre-tool-use":
            from . import pre_tool_use

            response = pre_tool_use.handle(payload, cfg, host=host)
        elif event == "session-end":
            from . import session_end

            response = session_end.handle(payload, cfg, host=host)
        else:
            response = {"continue": True}
    except Exception:
        _log.exception("hook %s failed; passing through", event)
        if cfg.strict:
            raise
        response = {"continue": True}

    if response is None:
        response = {}
    log_hook_exit(_log, cli_event=event, host=host, response=response)
    print(json.dumps(response, ensure_ascii=False))
    return 0
