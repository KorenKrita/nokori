from __future__ import annotations

import json
import sys

from ..config import Config


def dispatch(event: str, cfg: Config) -> int:
    """Hook event dispatcher. Reads stdin JSON, writes stdout JSON."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    try:
        if event == "session-start":
            from . import session_start

            response = session_start.handle(payload, cfg)
        elif event == "user-prompt-submit":
            from . import user_prompt_submit

            response = user_prompt_submit.handle(payload, cfg)
        elif event == "pre-tool-use":
            from . import pre_tool_use

            response = pre_tool_use.handle(payload, cfg)
        elif event == "session-end":
            from . import session_end

            response = session_end.handle(payload, cfg)
        else:
            response = {"continue": True}
    except Exception:
        from ..utils.logging import get_logger

        get_logger("nokori.hooks").exception("hook %s failed; passing through", event)
        response = {"continue": True}

    if response is None:
        response = {}
    print(json.dumps(response, ensure_ascii=False))
    return 0
