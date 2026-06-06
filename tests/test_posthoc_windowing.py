"""Tests for nokori.posthoc.windowing module."""

from __future__ import annotations

import pytest

from nokori.posthoc.windowing import (
    _is_topic_shift,
    _preceded_by_tool_sequence,
    _tokenize,
    _TOPIC_SHIFT_SIMILARITY_THRESHOLD,
    _LARGE_TURN_GAP,
    compute_event_window,
    extract_window_content,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turn(role: str, content: str, turn_index: int, **kwargs) -> dict:
    t = {"role": role, "content": content, "turn_index": turn_index}
    t.update(kwargs)
    return t


# ---------------------------------------------------------------------------
# 1. Empty/missing injection turn -> returns empty or minimal window
# ---------------------------------------------------------------------------


class TestEmptyOrMissingInjection:
    def test_empty_turns_list(self):
        assert compute_event_window([], 0) == []

    def test_injection_index_not_found(self):
        turns = [_turn("user", "hello", 0)]
        assert compute_event_window(turns, 99) == []

    def test_injection_index_none_equivalent(self):
        turns = [_turn("user", "hello", 1), _turn("assistant", "hi", 2)]
        assert compute_event_window(turns, 5) == []


# ---------------------------------------------------------------------------
# 2. Single turn after injection -> included in window
# ---------------------------------------------------------------------------


class TestSingleTurnAfterInjection:
    def test_single_following_turn_included(self):
        turns = [
            _turn("user", "implement the database migration handler", 0),
            _turn("assistant", "I'll implement the database migration handler now", 1),
        ]
        window = compute_event_window(turns, 0)
        assert len(window) == 2
        assert window[0]["turn_index"] == 0
        assert window[1]["turn_index"] == 1

    def test_injection_only_turn(self):
        turns = [_turn("user", "do the thing", 3)]
        window = compute_event_window(turns, 3)
        assert len(window) == 1
        assert window[0]["turn_index"] == 3


# ---------------------------------------------------------------------------
# 3. Topic shift via low lexical similarity (>5 tokens) — window stops before shift
# ---------------------------------------------------------------------------


class TestTopicShiftLexical:
    def test_dissimilar_user_message_triggers_shift(self):
        # Injection about database migrations, then totally unrelated content
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "I'll implement the database migration handler", 1),
            _turn("user", "completely unrelated purple elephant circus performance tonight", 2),
        ]
        window = compute_event_window(turns, 0)
        # Turn 2 has zero lexical overlap with injection and >=5 tokens
        assert len(window) == 2
        assert window[-1]["turn_index"] == 1

    def test_similar_message_does_not_trigger_shift(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "implementing the database migration handler now", 1),
            _turn("user", "also add rollback logic to the database migration handler", 2),
        ]
        window = compute_event_window(turns, 0)
        assert len(window) == 3


# ---------------------------------------------------------------------------
# 4. Short affirmative messages (<5 tokens) do NOT trigger topic shift
# ---------------------------------------------------------------------------


class TestShortAffirmativeMessages:
    def test_yes_does_not_trigger_shift(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "Shall I proceed with database migration?", 1),
            _turn("user", "yes", 2),
            _turn("assistant", "Implementing database migration now", 3),
        ]
        window = compute_event_window(turns, 0)
        # "yes" is <5 tokens, should not trigger shift
        assert any(t["turn_index"] == 2 for t in window)

    def test_ok_sure_do_it(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "confirm?", 1),
            _turn("user", "ok sure do it", 2),
            _turn("assistant", "done with database migration", 3),
        ]
        window = compute_event_window(turns, 0)
        assert any(t["turn_index"] == 2 for t in window)


# ---------------------------------------------------------------------------
# 5. Task change markers trigger topic shift
# ---------------------------------------------------------------------------


class TestTaskChangeMarkers:
    def test_now_lets_triggers_shift(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "done with the database migration", 1),
            _turn("user", "now let's work on the authentication module", 2),
        ]
        window = compute_event_window(turns, 0)
        assert all(t["turn_index"] != 2 for t in window)

    def test_switching_to_triggers_shift(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "done", 1),
            _turn("user", "switching to the frontend styling task", 2),
        ]
        window = compute_event_window(turns, 0)
        assert all(t["turn_index"] != 2 for t in window)

    def test_moving_on_triggers_shift(self):
        turns = [
            _turn("user", "implement the database migration handler", 0),
            _turn("assistant", "done", 1),
            _turn("user", "moving on to something else entirely", 2),
        ]
        window = compute_event_window(turns, 0)
        assert all(t["turn_index"] != 2 for t in window)


# ---------------------------------------------------------------------------
# 6. Tool sequence detection — single tool call vs 2+ consecutive tool calls
# ---------------------------------------------------------------------------


class TestToolSequenceDetection:
    def test_single_tool_call_not_sequence(self):
        turns = [
            _turn("user", "run the tests", 0),
            _turn("assistant", "running tests", 1, tool_name="bash"),
            _turn("user", "looks good", 2),
        ]
        # preceded_by_tool_sequence checks turn at index 2 (list index)
        # prev is turn at list index 1 which has tool_name -> True
        assert _preceded_by_tool_sequence(turns, 2) is True

    def test_no_tool_before_user(self):
        turns = [
            _turn("user", "hello", 0),
            _turn("assistant", "hi there", 1),
            _turn("user", "thanks", 2),
        ]
        assert _preceded_by_tool_sequence(turns, 2) is False

    def test_current_idx_too_small(self):
        turns = [_turn("user", "hi", 0), _turn("assistant", "hello", 1)]
        assert _preceded_by_tool_sequence(turns, 0) is False
        assert _preceded_by_tool_sequence(turns, 1) is False

    def test_tool_role_detected(self):
        turns = [
            _turn("user", "run it", 0),
            _turn("assistant", "calling tool", 1),
            _turn("tool", "result output", 2),
            _turn("user", "ok", 3),
        ]
        assert _preceded_by_tool_sequence(turns, 3) is True


# ---------------------------------------------------------------------------
# 7. Large turn gap (window length >= _LARGE_TURN_GAP) terminates window
# ---------------------------------------------------------------------------


class TestLargeTurnGap:
    def test_window_terminates_after_large_gap_with_low_overlap(self):
        # Build a window of _LARGE_TURN_GAP turns with drifting content
        injection = _turn("user", "implement the database migration handler for postgres", 0)
        turns = [injection]
        for i in range(1, _LARGE_TURN_GAP + 2):
            # Content drifts away completely from injection
            turns.append(_turn(
                "assistant" if i % 2 else "user",
                f"completely random xyz purple elephant {i} circus performance tonight",
                i,
            ))

        window = compute_event_window(turns, 0)
        # Window should terminate before reaching all turns due to low overlap
        assert len(window) < len(turns)

    def test_window_continues_if_content_stays_relevant(self):
        injection = _turn("user", "implement the database migration handler for postgres", 0)
        turns = [injection]
        for i in range(1, _LARGE_TURN_GAP + 2):
            turns.append(_turn(
                "assistant" if i % 2 else "user",
                "continuing work on the database migration handler for postgres",
                i,
            ))

        window = compute_event_window(turns, 0)
        assert len(window) == len(turns)


# ---------------------------------------------------------------------------
# 8. Embedding failure graceful degradation
# ---------------------------------------------------------------------------


class TestEmbeddingDegradation:
    def test_embedding_fn_raises_exception(self):
        def bad_embedding(text: str) -> list[float]:
            raise RuntimeError("embedding service down")

        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "doing the database migration now", 1),
        ]
        # Should not raise, degrades to lexical-only
        window = compute_event_window(turns, 0, embedding_fn=bad_embedding)
        assert len(window) == 2

    def test_embedding_fn_returns_none(self):
        def none_embedding(text: str):
            return None

        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "doing the database migration now", 1),
        ]
        window = compute_event_window(turns, 0, embedding_fn=none_embedding)
        assert len(window) == 2

    def test_embedding_detects_shift_when_working(self):
        # Embedding returns orthogonal vectors -> similarity ~0 -> shift
        def mock_embedding(text: str) -> list[float]:
            if "database" in text:
                return [1.0, 0.0, 0.0]
            return [0.0, 1.0, 0.0]  # orthogonal

        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("user", "tell me about purple elephants in the zoo today", 1),
        ]
        window = compute_event_window(turns, 0, embedding_fn=mock_embedding)
        # Embedding similarity is 0.0 < 0.3 threshold -> shift detected
        assert len(window) == 1


# ---------------------------------------------------------------------------
# 9. Window includes the boundary turn for tool sequences (asymmetric behavior)
# ---------------------------------------------------------------------------


class TestToolSequenceBoundaryInclusion:
    def test_user_turn_after_tool_is_included_then_stops(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "running database migration tool", 1, tool_name="bash"),
            _turn("user", "looks good with the database migration output", 2),
        ]
        window = compute_event_window(turns, 0)
        # The user turn at index 2 should be included (boundary turn) but window stops
        assert any(t["turn_index"] == 2 for t in window)
        assert len(window) == 3

    def test_turns_after_boundary_excluded(self):
        turns = [
            _turn("user", "implement the database migration handler for postgres", 0),
            _turn("assistant", "running database migration tool", 1, tool_name="bash"),
            _turn("user", "ok the database migration looks good", 2),
            _turn("assistant", "great, anything else about database migration?", 3),
        ]
        window = compute_event_window(turns, 0)
        # Window includes up to the user turn after tool, then stops
        assert window[-1]["turn_index"] == 2


# ---------------------------------------------------------------------------
# 10. extract_window_content handles various turn formats
# ---------------------------------------------------------------------------


class TestExtractWindowContent:
    def test_empty_window(self):
        assert extract_window_content([]) == ""

    def test_basic_turn(self):
        window = [_turn("user", "hello world", 0)]
        result = extract_window_content(window)
        assert "[Turn 0] user: hello world" in result

    def test_tool_turn_format(self):
        window = [_turn("assistant", "running command", 1, tool_name="bash")]
        result = extract_window_content(window)
        assert "[Turn 1] assistant (tool: bash): running command" in result

    def test_missing_content_key(self):
        window = [{"role": "user", "turn_index": 5}]
        result = extract_window_content(window)
        assert "[Turn 5] user:" in result

    def test_missing_role_key(self):
        window = [{"content": "some text", "turn_index": 3}]
        result = extract_window_content(window)
        assert "[Turn 3] unknown: some text" in result

    def test_missing_turn_index(self):
        window = [{"role": "user", "content": "hi"}]
        result = extract_window_content(window)
        assert "[Turn ?] user: hi" in result

    def test_content_truncated_at_2000(self):
        long_content = "x" * 3000
        window = [_turn("user", long_content, 0)]
        result = extract_window_content(window)
        # Content should be truncated to 2000 chars
        assert len(result) < 3000

    def test_multiple_turns_joined(self):
        window = [
            _turn("user", "first", 0),
            _turn("assistant", "second", 1),
        ]
        result = extract_window_content(window)
        assert "[Turn 0] user: first" in result
        assert "[Turn 1] assistant: second" in result
        assert "\n\n" in result
