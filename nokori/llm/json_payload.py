"""Extract JSON from LLM text that may include thinking blocks or markdown fences."""
from __future__ import annotations

import json
import re
from typing import Any

# Reasoning / thinking wrappers (models often emit these before the JSON answer).
_THINKING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"`[\s\S]*?`", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL | re.IGNORECASE),
)

_FENCE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def strip_thinking_blocks(text: str) -> str:
    """Remove known thinking/reasoning wrappers; repeat until stable."""
    prev = None
    out = text
    while prev != out:
        prev = out
        for pat in _THINKING_PATTERNS:
            out = pat.sub("", out)
    return out.strip()


def strip_fence(text: str) -> str:
    """Drop a single outer markdown code fence if present."""
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _iter_fence_bodies(text: str) -> list[str]:
    bodies = [m.group(1).strip() for m in _FENCE_BLOCK_RE.finditer(text)]
    if text.strip().startswith("```"):
        bodies.append(strip_fence(text))
    return bodies


def _balanced_json_slice(text: str, open_ch: str, close_ch: str) -> str | None:
    """Last valid JSON array/object slice by bracket matching (fallback)."""
    end = text.rfind(close_ch)
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            ch = text[i]
            if ch == close_ch:
                depth += 1
            elif ch == open_ch:
                depth -= 1
                if depth == 0:
                    chunk = text[i : end + 1]
                    try:
                        json.loads(chunk)
                    except json.JSONDecodeError:
                        break
                    return chunk
        end = text.rfind(close_ch, 0, end)
    return None


def _loads_candidate(fragment: str) -> Any | None:
    fragment = fragment.strip()
    if not fragment:
        return None
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        return None


def parse_json_payload(raw: str) -> Any | None:
    """Parse the first valid JSON value from model output (thinking/fence tolerant)."""
    if not raw or not raw.strip():
        return None

    stripped = strip_thinking_blocks(raw.strip())
    candidates: list[str] = []

    if stripped:
        candidates.append(stripped)

    candidates.extend(_iter_fence_bodies(raw))
    candidates.extend(_iter_fence_bodies(stripped))

    for body in reversed(candidates):
        parsed = _loads_candidate(body)
        if parsed is not None:
            return parsed

    balanced: list[str] = []
    for source in (stripped, raw.strip()):
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            chunk = _balanced_json_slice(source, open_ch, close_ch)
            if chunk:
                balanced.append(chunk)
    if balanced:
        best: Any | None = None
        best_len = -1
        for chunk in balanced:
            parsed = _loads_candidate(chunk)
            if parsed is not None and len(chunk) > best_len:
                best = parsed
                best_len = len(chunk)
        if best is not None:
            return best

    # Final fallback: json-repair for truncated/malformed JSON
    return _repair_json(stripped or raw.strip())


def _repair_json(text: str) -> Any | None:
    """Use json-repair library as last resort for malformed LLM output."""
    try:
        from json_repair import repair_json
        result = repair_json(text, return_objects=True)
        if result is not None and result != "" and result != [] and result != {}:
            if not isinstance(result, (dict, list)):
                return None
            return result
    except Exception:
        pass
    return None
