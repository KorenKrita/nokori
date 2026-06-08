from nokori.extract.extractor import _parse_candidates, extract
from nokori.llm.json_payload import parse_json_payload


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    def complete_messages(self, system, user, *, max_tokens=2000, timeout=30):
        return self.response


MINIMAX_STYLE = """<think>
Let me analyze this transcript for behavioral rules.
The user said 没bug的你说什么 — do not invent bugs in review.
</think>
```json
[
  {
    "trigger": "Code review of a clean PR where there are no real bugs",
    "trigger_variants": ["Reviewing a PR with no functional issues"],
    "search_terms": {"en": ["code review", "clean PR"], "zh": ["没bug", "代码审查"]},
    "behavior": "Listing minor nits as BUG-1 in the review summary",
    "action": "Say the PR is clean when there are no real bugs; reserve BUG for real defects",
    "rationale": "User said 没bug的你说什么 after false bug labels",
    "source_type": "correction",
    "confidence": "high"
  }
]
```"""


def test_parse_json_payload_strips_redacted_thinking_and_fence():
    data = parse_json_payload(MINIMAX_STYLE)
    assert isinstance(data, list)
    assert data[0]["trigger"].startswith("Code review")


def test_parse_candidates_minimax_style():
    cands, ok = _parse_candidates(MINIMAX_STYLE)
    assert ok is True
    assert len(cands) == 1
    assert cands[0].trigger is not None


def test_extract_end_to_end_with_thinking_prefix():
    cands, ok = extract("[User] fix the review\n", FakeLLM(MINIMAX_STYLE))
    assert ok and len(cands) == 1


def test_parse_json_payload_think_tags():
    raw = (
        "reasoning here\n"
        '{"relationships": [{"existing_id": "abc", "judgment": "A", "reasoning": "same"}]}'
    )
    data = parse_json_payload(raw)
    assert isinstance(data, dict)
    assert data["relationships"][0]["judgment"] == "A"


def test_parse_json_payload_prefers_last_fence_when_multiple():
    raw = (
        "```json\n[]\n```\n"
        "<think>draft</think>\n"
        "```json\n{\"x\": 1}\n```"
    )
    assert parse_json_payload(raw) == {"x": 1}


def test_parse_json_payload_thinking_wrapped_dict():
    payload = (
        "merge reasoning\n"
        '{"relationships": [{"existing_id": "x1", "judgment": "E", "reasoning": "ok"}]}'
    )
    out = parse_json_payload(payload)
    assert out is not None
    assert out["relationships"][0]["judgment"] == "E"
