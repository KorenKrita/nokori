"""Event window boundary detection for posthoc evaluation (spec section 10.1).

Determines the bounded transcript window for each fire event:
- From injection turn
- Until clear topic shift, next user prompt after tool sequence, or session end

Topic shift detection uses deterministic heuristics:
- Low lexical similarity between turns
- New tool sequence unrelated to rule scope/tool tags
- Explicit task change markers
- Large turn gap without rule-relevant action

If topic shift is uncertain, uses the shorter window (conservative).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOPIC_SHIFT_SIMILARITY_THRESHOLD = 0.15
_LARGE_TURN_GAP = 5
_TASK_CHANGE_MARKERS = (
    "now let's",
    "moving on",
    "next task",
    "switching to",
    "different topic",
    "new question",
    "unrelated",
    "另一个问题",
    "接下来",
    "换个话题",
    "下一个任务",
)

_TOKEN_SPLIT_RE = re.compile(r"[\s/\-_.,:;!?\"'`()\[\]{}]+")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_event_window(
    turns: list[dict[str, Any]],
    injection_turn_index: int,
    rule_tool_tags: list[str] | None = None,
    embedding_fn: Callable[[str], list[float]] | None = None,
) -> list[dict[str, Any]]:
    """Compute bounded transcript window for a fire event.

    Args:
        turns: List of session turns, each with 'role', 'content', 'turn_index',
               and optionally 'tool_name', 'tool_input'.
        injection_turn_index: The turn_index where the rule was injected.
        rule_tool_tags: Tool tags from the injected rule's scope (for relevance).
        embedding_fn: Optional callable that maps text to an embedding vector.
                      If provided, enables embedding-based topic shift detection.

    Returns:
        Bounded list of turns forming the evaluation window.
        Empty list if injection turn not found.
    """
    if not turns:
        return []

    start_idx = None
    for i, turn in enumerate(turns):
        if turn.get("turn_index") == injection_turn_index:
            start_idx = i
            break

    if start_idx is None:
        return []

    window_turns: list[dict[str, Any]] = [turns[start_idx]]
    injection_tokens = _tokenize(turns[start_idx].get("content", ""))

    # Pre-compute injection embedding if embedding_fn available
    injection_embedding: list[float] | None = None
    if embedding_fn is not None:
        injection_content = turns[start_idx].get("content", "")
        if injection_content:
            try:
                injection_embedding = embedding_fn(injection_content)
            except Exception:
                injection_embedding = None

    for i in range(start_idx + 1, len(turns)):
        turn = turns[i]

        if _is_topic_shift(
            turn,
            injection_tokens,
            window_turns,
            rule_tool_tags,
            embedding_fn=embedding_fn,
            injection_embedding=injection_embedding,
        ):
            break

        window_turns.append(turn)

        # Stop after next user prompt following a tool sequence
        if turn.get("role") == "user" and _preceded_by_tool_sequence(turns, i):
            break

    return window_turns


def extract_window_content(window_turns: list[dict[str, Any]]) -> str:
    """Format bounded window turns into text content for LLM evaluation."""
    if not window_turns:
        return ""

    parts: list[str] = []
    for turn in window_turns:
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        tool_name = turn.get("tool_name")
        turn_idx = turn.get("turn_index", "?")

        if tool_name:
            parts.append(f"[Turn {turn_idx}] {role} (tool: {tool_name}): {content[:2000]}")
        else:
            parts.append(f"[Turn {turn_idx}] {role}: {content[:2000]}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Topic shift detection (deterministic heuristics)
# ---------------------------------------------------------------------------


_EMBEDDING_SHIFT_THRESHOLD = 0.3


def _is_topic_shift(
    turn: dict[str, Any],
    injection_tokens: set[str],
    window_so_far: list[dict[str, Any]],
    rule_tool_tags: list[str] | None,
    *,
    embedding_fn: Callable[[str], list[float]] | None = None,
    injection_embedding: list[float] | None = None,
) -> bool:
    """Detect whether a turn represents a topic shift from the injection context.

    If embedding_fn is provided AND the turn is a user message, embedding-based
    similarity is checked first. If similarity < 0.3, it's a topic shift.
    Gracefully degrades to lexical-only if no embedding_fn or on error.

    Returns True if topic shift is detected (conservative: shorter window).
    """
    content = turn.get("content", "")

    # Embedding-based similarity check (optional, user messages only)
    if (
        embedding_fn is not None
        and injection_embedding is not None
        and turn.get("role") == "user"
        and content
    ):
        try:
            turn_embedding = embedding_fn(content)
            if turn_embedding and injection_embedding:
                similarity = _cosine_similarity(injection_embedding, turn_embedding)
                if similarity < _EMBEDDING_SHIFT_THRESHOLD:
                    return True
        except Exception:
            pass  # Degrade to lexical-only

    # Explicit task change markers
    content_lower = content.lower()
    for marker in _TASK_CHANGE_MARKERS:
        if marker in content_lower:
            return True

    # Large turn gap without relevance
    if len(window_so_far) >= _LARGE_TURN_GAP:
        recent_content = " ".join(t.get("content", "") for t in window_so_far[-3:])
        recent_tokens = _tokenize(recent_content)
        if not recent_tokens or not injection_tokens:
            return True
        overlap = len(injection_tokens & recent_tokens) / max(len(injection_tokens), 1)
        if overlap < _TOPIC_SHIFT_SIMILARITY_THRESHOLD:
            return True

    # Low lexical similarity with injection context
    # Skip for short messages (< 5 tokens) — they are likely affirmative continuations
    # ("yes", "ok", "sure", "do it"), not topic shifts.
    turn_tokens = _tokenize(content)
    if injection_tokens and turn_tokens and len(turn_tokens) >= 5:
        similarity = len(injection_tokens & turn_tokens) / max(len(injection_tokens), 1)
        if turn.get("role") == "user" and similarity < _TOPIC_SHIFT_SIMILARITY_THRESHOLD:
            return True

    # New tool unrelated to rule's tool scope
    if rule_tool_tags and turn.get("tool_name"):
        tool = turn["tool_name"].lower()
        tool_relevant = any(tag.lower() in tool for tag in rule_tool_tags)
        if not tool_relevant and turn.get("role") == "assistant" and len(window_so_far) >= 3:
            # Unrelated tool after injection — possible shift
            return True

    return False


def _preceded_by_tool_sequence(turns: list[dict[str, Any]], current_idx: int) -> bool:
    """Check if the current turn is preceded by tool calls (assistant tool use)."""
    if current_idx < 1:
        return False
    prev = turns[current_idx - 1]
    return prev.get("tool_name") is not None or prev.get("role") == "tool"


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase tokens."""
    if not text:
        return set()
    return {t for t in _TOKEN_SPLIT_RE.split(text.lower()) if len(t) >= 3}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(dot / (na * nb))
