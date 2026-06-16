from __future__ import annotations

from collections.abc import Iterable

from ..models import Turn

TOOL_ERROR_HEAD = 500
TOOL_ERROR_TAIL = 300


def _approx_tokens(text: str) -> int:
    # Conservative approximation: UTF-8 bytes / 3.
    # CJK = 3 bytes ≈ 1 token. ASCII = 1 byte ≈ 0.33 tokens (overestimates slightly).
    # Intentionally conservative to avoid exceeding budget.
    return max(1, len(text.encode("utf-8")) // 3)


def compress(turns: Iterable[Turn], budget_tokens: int = 100000) -> str:
    sections: list[str] = []
    for t in turns:
        if t.role == "human":
            sections.append(f"[User] {t.content.strip()}")
            continue
        if t.role == "assistant":
            c = t.content.strip()
            if c:
                sections.append(f"[Assistant] {c}")
            continue
        if t.role == "tool_use":
            sections.append(f"[Tool: {t.tool_name}] {t.input_summary}")
            continue
        if t.role == "tool_result":
            if t.is_error:
                err = t.error_line.strip()
                if len(err) > TOOL_ERROR_HEAD + TOOL_ERROR_TAIL + 5:
                    err = err[:TOOL_ERROR_HEAD] + "\n...\n" + err[-TOOL_ERROR_TAIL:]
                sections.append(f"[Result: ERROR] {err}")
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
    return text[:half] + "\n...[transcript truncated: middle omitted]...\n" + text[-half:]


def _pair_tools_and_results(sections: list[str]) -> list:
    """Pre-process sections into paired groups.

    Handles any mix of Tool/Result ordering. Results are assigned to
    pending (unmatched) Tools in FIFO order.

    Returns a list of items, each being either:
      - str: a non-tool line (User, Assistant, etc.)
      - list[tuple[str, str, bool]]: a group of (tool_line, result_line, is_error) pairs
    """
    out: list = []
    pending_tools: list[str] = []
    paired: list[tuple[str, str, bool]] = []
    i = 0

    def flush() -> None:
        nonlocal pending_tools, paired
        for tool_line in pending_tools:
            paired.append((tool_line, "[no result]", False))
        pending_tools = []
        if paired:
            out.append(paired)
            paired = []

    while i < len(sections):
        line = sections[i]

        if line.startswith("[Tool: "):
            pending_tools.append(line)
            i += 1
        elif line.startswith("[Result:"):
            is_error = line.startswith("[Result: ERROR]")
            if pending_tools:
                tool_line = pending_tools.pop(0)
                paired.append((tool_line, line, is_error))
            else:
                # Orphan result — emit as-is
                flush()
                out.append(line)
            i += 1
        else:
            flush()
            out.append(line)
            i += 1

    flush()
    return out


def _collapse_consecutive_tools(sections: list[str]) -> str:
    """Phase 1: Collapse 3+ consecutive same-tool successes into summary."""
    paired = _pair_tools_and_results(sections)
    out: list[str] = []

    for item in paired:
        if isinstance(item, str):
            out.append(item)
            continue

        # item is list of (tool_line, result_line, is_error) pairs
        i = 0
        while i < len(item):
            tool_line, result, is_error = item[i]

            if is_error:
                out.append(tool_line)
                out.append(result)
                i += 1
                continue

            # Count consecutive same-tool successes
            tool_name = _extract_tool_name(tool_line)
            run_start = i
            while i < len(item) and not item[i][2] and _extract_tool_name(item[i][0]) == tool_name:
                i += 1

            count = i - run_start
            if count >= 3:
                out.append(f"[Tool: {tool_name}] x{count} OK")
            else:
                for j in range(run_start, i):
                    out.append(item[j][0])
                    if item[j][1]:
                        out.append(item[j][1])

    return "\n".join(out)


def _collapse_tool_runs(sections: list[str]) -> str:
    """Phase 2: Collapse consecutive tool successes into summary, except Bash."""
    paired = _pair_tools_and_results(sections)
    out: list[str] = []

    for item in paired:
        if isinstance(item, str):
            out.append(item)
            continue

        tool_counts: dict[str, int] = {}

        for tool_line, result, is_error in item:
            if is_error:
                if tool_counts:
                    parts = [f"{n} x{c}" if c > 1 else n for n, c in tool_counts.items()]
                    out.append(f"[Tools OK] {', '.join(parts)}")
                    tool_counts = {}
                out.append(tool_line)
                if result:
                    out.append(result)
            elif _extract_tool_name(tool_line).lower() in ("bash", "shell"):
                if tool_counts:
                    parts = [f"{n} x{c}" if c > 1 else n for n, c in tool_counts.items()]
                    out.append(f"[Tools OK] {', '.join(parts)}")
                    tool_counts = {}
                out.append(tool_line)
                if result:
                    out.append(result)
            elif result and result != "[no result]":
                name = _extract_tool_name(tool_line)
                tool_counts[name] = tool_counts.get(name, 0) + 1
            else:
                # No result or pending — emit as-is
                if tool_counts:
                    parts = [f"{n} x{c}" if c > 1 else n for n, c in tool_counts.items()]
                    out.append(f"[Tools OK] {', '.join(parts)}")
                    tool_counts = {}
                out.append(tool_line)

        if tool_counts:
            parts = [f"{n} x{c}" if c > 1 else n for n, c in tool_counts.items()]
            out.append(f"[Tools OK] {', '.join(parts)}")

    return "\n".join(out)


def _extract_tool_name(line: str) -> str:
    """Extract tool name from '[Tool: Name] ...'"""
    if line.startswith("[Tool: "):
        end = line.index("]", 7)
        return line[7:end]
    return ""
