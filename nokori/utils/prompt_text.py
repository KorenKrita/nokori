"""Normalize user prompt text for stable hashing across hooks and transcripts."""

from __future__ import annotations

import re

_USER_QUERY_RE = re.compile(
    r"^\s*<user_query>\s*(.*?)\s*</user_query>\s*$",
    re.DOTALL | re.IGNORECASE,
)


def normalize_prompt_for_hash(text: str) -> str:
    """Strip Cursor-style wrappers so transcript tail matches beforeSubmitPrompt payload."""
    if not text:
        return ""
    m = _USER_QUERY_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text.strip()
