from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..constants import MAX_TRANSCRIPT_BYTES
from ..errors import NokoriError
from ..models import Turn, TurnRole
from ..utils.logging import get_logger

log = get_logger("nokori.extract.reader")


@dataclass(frozen=True)
class TranscriptMeta:
    path: Path
    mtime: float
    size: int


def stat(path: Path) -> TranscriptMeta:
    try:
        s = path.stat()
    except OSError as e:
        raise NokoriError(f"transcript not readable: {path}") from e
    return TranscriptMeta(path=path, mtime=s.st_mtime, size=s.st_size)


_LINE_SIZE_CAP = 5000


def _parse_jsonl_text(text: str, path: Path, label: str = "line") -> list[Turn]:
    out: list[Turn] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if len(line) > _LINE_SIZE_CAP:
            # Huge lines (e.g. Write with full file content) — extract minimal info
            out.extend(_parse_large_line(line))
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            log.warning("transcript parse skip path=%s %s=%d", path.name, label, lineno)
            continue
        out.extend(_parse_multi(entry))
    return out


_KNOWN_TYPES = frozenset(
    (
        "user",
        "human",
        "assistant",
        "text",
        "tool_use",
        "tool_result",
        "toolCall",
        "toolResult",
        "message",
    )
)


def _large_tool_use_turn(line: str) -> Turn:
    name_match = re.search(r'"(?:name|tool_name)"\s*:\s*"([^"]+)"', line[:3000])
    name = name_match.group(1) if name_match else "tool"
    return Turn(
        role="tool_use",
        content="",
        tool_name=name,
        input_summary="[large input truncated]",
    )


def _large_tool_result_turn(line: str) -> Turn:
    head = line[:3000]
    name_match = re.search(r'"(?:toolName|tool_name)"\s*:\s*"([^"]+)"', head)
    is_err = bool(
        re.search(r'"(?:isError|is_error|error)"\s*:\s*true', head)
    )
    error_line = ""
    if is_err:
        err_match = re.search(r'"(?:error_line|content)"\s*:\s*"([^"]{0,300})', head)
        error_line = err_match.group(1) if err_match else "error"
    return Turn(
        role="tool_result",
        content="",
        tool_name=name_match.group(1) if name_match else None,
        is_error=is_err,
        error_line=error_line,
    )


def _parse_large_nested_message(line: str) -> list[Turn]:
    head = line[:4000]
    role_match = re.search(r'"role"\s*:\s*"([^"]+)"', head)
    role = role_match.group(1) if role_match else None
    if role in ("toolResult", "tool_result"):
        return [_large_tool_result_turn(line)]
    if re.search(r'"type"\s*:\s*"(?:toolCall|tool_use)"', head):
        return [_large_tool_use_turn(line)]
    text_match = re.search(r'"text"\s*:\s*"([^"]{0,500})', head)
    content = text_match.group(1) if text_match else ""
    return [Turn(role=_normalize_role(role), content=content)] if role else []


def _parse_large_line(line: str) -> list[Turn]:
    """Fast-path for oversized JSONL lines. Extract type and tool name without full parse."""
    type_match = re.search(r'"type"\s*:\s*"([^"]+)"', line[:2000])
    if not type_match or type_match.group(1) not in _KNOWN_TYPES:
        # Fallback: attempt full parse for lines where regex can't find type
        try:
            entry = json.loads(line)
            return _parse_multi(entry)
        except Exception:
            log.debug("Skipping unparseable large line (len=%d)", len(line))
            return []
    t = type_match.group(1)
    if t == "message":
        return _parse_large_nested_message(line)
    if t in ("tool_use", "toolCall"):
        return [_large_tool_use_turn(line)]
    if t in ("tool_result", "toolResult"):
        return [_large_tool_result_turn(line)]
    if t in ("assistant", "text"):
        content_match = re.search(r'"(?:content|message)"\s*:\s*"([^"]{0,500})', line[:2000])
        content = content_match.group(1) if content_match else ""
        return [Turn(role="assistant", content=content)]
    if t in ("user", "human"):
        content_match = re.search(r'"(?:content|message)"\s*:\s*"([^"]{0,500})', line[:2000])
        content = content_match.group(1) if content_match else ""
        return [Turn(role="human", content=content)]
    return []


def read(path: Path) -> list[Turn]:
    """Parse a Claude Code JSONL transcript into Turn objects."""
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        raw = fh.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise NokoriError(f"transcript too large ({len(raw)} bytes; max {MAX_TRANSCRIPT_BYTES})")
    return _parse_jsonl_text(raw.decode("utf-8", errors="replace"), path)


def read_after(path: Path, byte_offset: int) -> tuple[list[Turn], int]:
    """Read turns added after byte_offset. Returns (turns, new_byte_offset).

    If byte_offset <= 0 or exceeds file size (truncation/sync conflict), falls
    back to full read from start.
    """
    if not path.exists():
        return [], 0
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, 2)
            if byte_offset <= 0 or byte_offset > size:
                return read(path), size
            delta = size - byte_offset
            if delta > MAX_TRANSCRIPT_BYTES:
                raise NokoriError(
                    f"transcript delta too large ({delta} bytes; max {MAX_TRANSCRIPT_BYTES})"
                )
            fh.seek(byte_offset)
            raw = fh.read()
    except OSError:
        return [], 0
    incremental = _parse_jsonl_text(raw.decode("utf-8", errors="replace"), path, "incremental_line")
    return incremental, size


def read_tail_user_turns(
    path: Path, limit: int = 3, *, end_offset: int | None = None
) -> list[Turn]:
    """Last N human turns, scanning backwards from end_offset (default: EOF)."""
    if limit <= 0 or not path.exists():
        return []
    human: list[Turn] = []
    block = 65536
    leftover = b""
    with open(path, "rb") as f:
        if end_offset is not None and end_offset > 0:
            pos = min(end_offset, f.seek(0, 2))
        else:
            pos = f.seek(0, 2)
        while pos > 0 and len(human) < limit:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step) + leftover
            parts = chunk.split(b"\n")
            leftover = parts[0]
            for raw in reversed(parts[1:]):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                for turn in _parse_multi(entry):
                    if turn.role == "human":
                        human.append(turn)
                        if len(human) >= limit:
                            break
                if len(human) >= limit:
                    break
        if len(human) < limit and leftover.strip():
            try:
                entry = json.loads(leftover.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                pass
            else:
                human.extend(turn for turn in _parse_multi(entry) if turn.role == "human")
    human.reverse()
    return human[-limit:]


def _parse(entry: dict) -> Turn | None:
    multi = _parse_multi(entry)
    return multi[0] if multi else None


def _parse_multi(entry: dict) -> list[Turn]:
    blocks = _parse_message_blocks(entry)
    if blocks:
        return blocks
    single = _parse_legacy(entry)
    return [single] if single else []


def _parse_message_blocks(entry: dict) -> list[Turn]:
    """Cursor / Pi / OMP nested message payloads and content[] blocks."""
    role = entry.get("role") or entry.get("type")
    msg = entry.get("message")
    content = None
    if isinstance(msg, dict):
        role = msg.get("role") or role
        if role in ("toolResult", "tool_result"):
            return [_tool_result_turn(msg)]
        content = msg.get("content")
    if content is None:
        content = entry.get("content")
    if not isinstance(content, list):
        return []
    return _content_blocks_to_turns(role, content)


def _content_blocks_to_turns(role: str | None, blocks: list) -> list[Turn]:
    turns: list[Turn] = []
    texts: list[str] = []
    norm_role = _normalize_role(role)

    def flush_text() -> None:
        if not texts:
            return
        turns.append(Turn(role=norm_role, content="\n".join(texts)))
        texts.clear()

    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            piece = str(block.get("text", ""))
            if piece:
                texts.append(piece)
        elif kind in ("tool_use", "toolCall"):
            flush_text()
            inp = block.get("input")
            if inp is None:
                inp = block.get("arguments")
            turns.append(_tool_use_turn(block.get("name") or block.get("tool_name"), inp))
        elif kind in ("tool_result", "toolResult"):
            flush_text()
            turns.append(_tool_result_turn(block))

    flush_text()
    return turns


def _tool_use_turn(tool_name: object, inp: object) -> Turn:
    name = str(tool_name or "tool")
    return Turn(
        role="tool_use",
        content="",
        tool_name=name,
        input_summary=_summarize_tool_input(name, inp),
    )


def _tool_result_turn(payload: dict) -> Turn:
    tool_name = payload.get("toolName") or payload.get("tool_name")
    content = _coerce_text(payload.get("content") or payload.get("output") or "")
    is_err = bool(payload.get("isError") or payload.get("is_error") or payload.get("error"))
    first_err_line = ""
    if is_err:
        first_err_line = content.splitlines()[0][:200] if content else ""
    return Turn(
        role="tool_result",
        content=content[:400] if not is_err else "",
        tool_name=str(tool_name) if tool_name else None,
        is_error=is_err,
        error_line=first_err_line,
    )


def _normalize_role(role: str | None) -> TurnRole:
    if role in ("user", "human"):
        return "human"
    if role == "assistant":
        return "assistant"
    if role in ("tool_use", "toolCall"):
        return "tool_use"
    if role in ("tool_result", "toolResult"):
        return "tool_result"
    return "assistant"


def _parse_legacy(entry: dict) -> Turn | None:
    t = entry.get("type") or entry.get("role")
    if t == "user" or t == "human":
        content = _coerce_text(entry.get("message") or entry.get("content") or entry)
        return Turn(role="human", content=content)
    if t == "assistant":
        content = _coerce_text(entry.get("message") or entry.get("content") or entry)
        return Turn(role="assistant", content=content)
    if t in ("tool_use", "toolCall"):
        inp = entry.get("input")
        if inp is None:
            inp = entry.get("arguments")
        return _tool_use_turn(entry.get("name") or entry.get("tool_name"), inp)
    if t in ("tool_result", "toolResult"):
        return _tool_result_turn(entry)
    return None


_TOOL_LIMITS: tuple[tuple[re.Pattern, int], ...] = (
    (re.compile(r"(?:^|[_:\-])bash(?:$|[_:\-])", re.I), 0),
    (re.compile(r"(?:^|[_:\-])read(?:$|[_:\-])", re.I), 0),
    (re.compile(r"(?:^|[_:\-])edit(?:$|[_:\-])", re.I), 100),
    (re.compile(r"(?:^|[_:\-])write(?:$|[_:\-])", re.I), 100),
    (re.compile(r"(?:^|[_:\-])grep(?:$|[_:\-])", re.I), 50),
)
_DEFAULT_HEAD = 200
_DEFAULT_TAIL = 100


_VALUE_CAP = 800
_RAW_INPUT_MAX = 2000


def _summarize_tool_input(tool_name: str, inp: object) -> str:
    """Produce tool input summary.

    bash/read: no secondary truncation (values capped at 800 chars, strings at 2000)
    edit/write: head 100
    grep: head 50
    others: head 200 + tail 100
    """
    if not inp:
        return ""
    if isinstance(inp, str):
        raw = inp[:_RAW_INPUT_MAX] if len(inp) > _RAW_INPUT_MAX else inp
    elif isinstance(inp, dict):
        capped = {}
        for k, v in inp.items():
            if isinstance(v, str):
                capped[k] = v[:_VALUE_CAP] + "..." if len(v) > _VALUE_CAP else v
            else:
                s = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
                capped[k] = s[:_VALUE_CAP] + "..." if len(s) > _VALUE_CAP else s
        raw = json.dumps(capped, ensure_ascii=False)
    else:
        raw = str(inp)[:_RAW_INPUT_MAX]

    lower = tool_name.lower()
    for pattern, limit in _TOOL_LIMITS:
        if pattern.search(lower):
            if limit == 0:
                return raw[:_RAW_INPUT_MAX] if len(raw) > _RAW_INPUT_MAX else raw
            if len(raw) <= limit:
                return raw
            return raw[:limit]

    cap = _DEFAULT_HEAD + _DEFAULT_TAIL
    if len(raw) <= cap:
        return raw
    return raw[:_DEFAULT_HEAD] + "\n...\n" + raw[-_DEFAULT_TAIL:]


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
                elif "content" in item:
                    parts.append(_coerce_text(item["content"]))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(value, dict):
        if "text" in value:
            return str(value["text"])
        if "content" in value:
            return _coerce_text(value["content"])
        return json.dumps(value, ensure_ascii=False)
    return str(value)
