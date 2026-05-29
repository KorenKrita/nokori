from __future__ import annotations

from collections.abc import Iterable

from ..models import Turn

ASSISTANT_HEAD = 200
ASSISTANT_TAIL = 100
TOOL_INPUT_LIMIT = 100
TOOL_ERROR_LIMIT = 120


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def compress(turns: Iterable[Turn], budget_tokens: int = 30000) -> str:
    sections: list[str] = []
    for t in turns:
        if t.role == "human":
            sections.append(f"[User] {t.content.strip()}")
            continue
        if t.role == "assistant":
            c = t.content.strip()
            if len(c) > ASSISTANT_HEAD + ASSISTANT_TAIL + 5:
                c = c[:ASSISTANT_HEAD] + "\n...\n" + c[-ASSISTANT_TAIL:]
            sections.append(f"[Assistant] {c}")
            continue
        if t.role == "tool_use":
            sections.append(
                f"[Tool: {t.tool_name}] {t.input_summary[:TOOL_INPUT_LIMIT]}"
            )
            continue
        if t.role == "tool_result":
            if t.is_error:
                sections.append(f"[Result: ERROR] {t.error_line[:TOOL_ERROR_LIMIT]}")
            else:
                sections.append("[Result: OK]")
            continue

    text = "\n".join(sections)
    if _approx_tokens(text) <= budget_tokens:
        return text

    keep_chars = budget_tokens * 3
    if len(text) > keep_chars:
        text = text[-keep_chars:]
        cut = text.find("\n[")
        if cut > 0:
            text = text[cut + 1 :]
    return text
