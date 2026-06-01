from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..constants import MAX_TRANSCRIPT_BYTES
from ..errors import NokoriError
from ..models import Turn
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


def _parse_jsonl_text(text: str, path: Path, label: str = "line") -> list[Turn]:
    out: list[Turn] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            log.warning("transcript parse skip path=%s %s=%d", path.name, label, lineno)
            continue
        out.extend(_parse_multi(entry))
    return out


def read(path: Path) -> list[Turn]:
    """Parse a Claude Code JSONL transcript into Turn objects."""
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        raw = fh.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise NokoriError(
            f"transcript too large ({len(raw)} bytes; max {MAX_TRANSCRIPT_BYTES})"
        )
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


def read_tail_user_turns(path: Path, limit: int = 3, *, end_offset: int | None = None) -> list[Turn]:
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
                for turn in _parse_multi(entry):
                    if turn.role == "human":
                        human.append(turn)
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
    """Cursor / nested message.content[] (text, tool_use, tool_result)."""
    role = entry.get("role") or entry.get("type")
    msg = entry.get("message")
    content = None
    if isinstance(msg, dict):
        role = msg.get("role") or role
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
        elif kind == "tool_use":
            flush_text()
            inp = block.get("input") or {}
            turns.append(
                Turn(
                    role="tool_use",
                    content="",
                    tool_name=block.get("name") or block.get("tool_name") or "tool",
                    input_summary=_coerce_text(inp)[:200],
                )
            )
        elif kind == "tool_result":
            flush_text()
            content = _coerce_text(block.get("content") or block.get("output") or "")
            is_err = bool(block.get("is_error") or block.get("error"))
            first_err_line = ""
            if is_err:
                first_err_line = content.splitlines()[0][:200] if content else ""
            turns.append(
                Turn(
                    role="tool_result",
                    content=content[:400] if not is_err else "",
                    is_error=is_err,
                    error_line=first_err_line,
                )
            )

    flush_text()
    return turns


def _normalize_role(role: str | None) -> str:
    if role in ("user", "human"):
        return "human"
    if role == "assistant":
        return "assistant"
    return role or "assistant"


def _parse_legacy(entry: dict) -> Turn | None:
    t = entry.get("type") or entry.get("role")
    if t == "user" or t == "human":
        content = _coerce_text(entry.get("message") or entry.get("content") or entry)
        return Turn(role="human", content=content)
    if t == "assistant":
        content = _coerce_text(entry.get("message") or entry.get("content") or entry)
        return Turn(role="assistant", content=content)
    if t == "tool_use":
        name = entry.get("name") or entry.get("tool_name") or "tool"
        inp = entry.get("input") or {}
        return Turn(
            role="tool_use",
            content="",
            tool_name=name,
            input_summary=_coerce_text(inp)[:200],
        )
    if t == "tool_result":
        content = _coerce_text(entry.get("content") or entry.get("output") or "")
        is_err = bool(entry.get("is_error") or entry.get("error"))
        first_err_line = ""
        if is_err:
            first_err_line = content.splitlines()[0][:200] if content else ""
        return Turn(
            role="tool_result",
            content=content[:400] if not is_err else "",
            is_error=is_err,
            error_line=first_err_line,
        )
    return None


def _coerce_text(value) -> str:
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
