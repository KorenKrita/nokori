from __future__ import annotations

from collections.abc import Iterable

from ..models import Turn

TOOL_ERROR_LIMIT = 300


def _approx_tokens(text: str) -> int:
    wide = sum(1 for c in text if ord(c) > 127)
    narrow = len(text) - wide
    return max(1, wide + narrow // 4)


def compress(turns: Iterable[Turn], budget_tokens: int = 100000) -> str:
    sections: list[str] = []
    for t in turns:
        if t.role == "human":
            sections.append(f"[User] {t.content.strip()}")
            continue
        if t.role == "assistant":
            sections.append(f"[Assistant] {t.content.strip()}")
            continue
        if t.role == "tool_use":
            sections.append(f"[Tool: {t.tool_name}] {t.input_summary}")
            continue
        if t.role == "tool_result":
            if t.is_error:
                sections.append(f"[Result: ERROR] {t.error_line[:TOOL_ERROR_LIMIT]}")
            else:
                sections.append("[Result: OK]")
            continue

    text = "\n".join(sections)
    tokens = _approx_tokens(text)
    if tokens <= budget_tokens:
        return text

    # Phase 1: collapse consecutive same-tool successes
    text = _collapse_consecutive_tools(sections)
    tokens = _approx_tokens(text)
    if tokens <= budget_tokens:
        return text

    # Phase 2: collapse consecutive different-tool successes into tool name lists
    text = _collapse_tool_runs(sections)
    tokens = _approx_tokens(text)
    if tokens <= budget_tokens:
        return text

    # Phase 3: hard truncate head+tail as last resort
    keep_chars = max(400, int(len(text) * budget_tokens / tokens))
    half = keep_chars // 2
    return (
        text[:half]
        + "\n...[transcript truncated: middle omitted]...\n"
        + text[-half:]
    )


def _collapse_consecutive_tools(sections: list[str]) -> str:
    """Collapse runs of consecutive same-tool [Tool:X]+[Result:OK] into summary lines.

    Preserves: User turns, Assistant turns, errors, and isolated tool calls.
    Collapses: 3+ consecutive successful calls to the same tool.
    """
    out: list[str] = []
    i = 0
    while i < len(sections):
        line = sections[i]

        if not line.startswith("[Tool: "):
            out.append(line)
            i += 1
            continue

        tool_name = _extract_tool_name(line)
        run_start = i
        success_count = 0

        while i < len(sections):
            if sections[i].startswith(f"[Tool: {tool_name}]"):
                if i + 1 < len(sections) and sections[i + 1] == "[Result: OK]":
                    success_count += 1
                    i += 2
                else:
                    break
            else:
                break

        if success_count >= 3:
            out.append(f"[Tool: {tool_name}] x{success_count} OK")
        else:
            out.extend(sections[run_start:i])

    return "\n".join(out)


def _collapse_tool_runs(sections: list[str]) -> str:
    """More aggressive: collapse any consecutive successful tool calls into a summary.

    Groups all consecutive [Tool:X][Result:OK] pairs (regardless of tool name)
    into a single line listing tool names and count.
    """
    out: list[str] = []
    i = 0
    while i < len(sections):
        line = sections[i]

        if not line.startswith("[Tool: "):
            out.append(line)
            i += 1
            continue

        tool_counts: dict[str, int] = {}
        run_start = i

        while i < len(sections) and sections[i].startswith("[Tool: "):
            tool_name = _extract_tool_name(sections[i])
            if i + 1 < len(sections) and sections[i + 1] == "[Result: OK]":
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                i += 2
            elif i + 1 < len(sections) and sections[i + 1].startswith("[Result: ERROR]"):
                # Preserve errors inline
                out.append(sections[i])
                out.append(sections[i + 1])
                i += 2
                break
            else:
                out.append(sections[i])
                i += 1
                break

        if tool_counts:
            parts = [f"{name} x{count}" if count > 1 else name for name, count in tool_counts.items()]
            out.append(f"[Tools OK] {', '.join(parts)}")

    return "\n".join(out)


def _extract_tool_name(line: str) -> str:
    """Extract tool name from '[Tool: Name] ...'"""
    if line.startswith("[Tool: "):
        end = line.index("]", 7)
        return line[7:end]
    return ""
