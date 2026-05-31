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


def read(path: Path) -> list[Turn]:
    """Parse a Claude Code JSONL transcript into Turn objects.

    The format Claude Code uses is a stream of records, one per line, with a
    `type` discriminating user / assistant / tool / etc. We tolerate unknown
    types and malformed lines (logged as warnings, skipped).
    """
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        raw = fh.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise NokoriError(
            f"transcript too large ({len(raw)} bytes; max {MAX_TRANSCRIPT_BYTES})"
        )
    out: list[Turn] = []
    text = raw.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            log.warning("transcript parse skip path=%s line=%d", path.name, lineno)
            continue
        turn = _parse(entry)
        if turn:
            out.append(turn)
    return out


def read_tail_user_turns(path: Path, limit: int = 3) -> list[Turn]:
    """Last N user turns without loading the full transcript into memory."""
    if limit <= 0 or not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    human: list[Turn] = []
    block = 65536
    leftover = b""
    with open(path, "rb") as f:
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
                turn = _parse(entry)
                if turn and turn.role == "human":
                    human.append(turn)
                    if len(human) >= limit:
                        break
        if len(human) < limit and leftover.strip():
            try:
                entry = json.loads(leftover.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                pass
            else:
                turn = _parse(entry)
                if turn and turn.role == "human":
                    human.append(turn)

    human.reverse()
    return human[-limit:]


def _parse(entry: dict) -> Turn | None:
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
