"""Tests for incremental transcript reading (read_after)."""
import json

from nokori.extract.reader import read_after, read_tail_user_turns


def _write_turns(path, turns):
    lines = []
    for role, content in turns:
        lines.append(json.dumps({"type": role, "message": content}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _append_turns(path, turns):
    lines = []
    for role, content in turns:
        lines.append(json.dumps({"type": role, "message": content}))
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_read_after_zero_offset_returns_all(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_turns(path, [("user", "hello"), ("assistant", "hi")])
    turns, offset = read_after(path, 0)
    assert len(turns) == 2
    assert offset == path.stat().st_size


def test_read_after_returns_only_new_turns(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_turns(path, [("user", "first"), ("assistant", "reply1")])
    mid_offset = path.stat().st_size

    _append_turns(path, [("user", "second"), ("assistant", "reply2")])
    turns, new_offset = read_after(path, mid_offset)
    human_turns = [t for t in turns if t.role == "human"]
    assert any("second" in t.content for t in human_turns)
    assert not any("first" in t.content for t in human_turns)
    assert new_offset == path.stat().st_size


def test_read_after_fallback_on_truncation(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_turns(path, [("user", "hello")])
    size = path.stat().st_size
    turns, offset = read_after(path, size + 1000)
    assert len(turns) == 1
    assert turns[0].content == "hello"
    assert offset == size


def test_context_turns_via_read_tail(tmp_path):
    """Context stitching is caller's responsibility — test read_tail_user_turns with end_offset."""
    path = tmp_path / "t.jsonl"
    _write_turns(path, [
        ("user", "ctx1"),
        ("assistant", "r1"),
        ("user", "ctx2"),
        ("assistant", "r2"),
    ])
    mid_offset = path.stat().st_size
    _append_turns(path, [("user", "new_msg")])

    context = read_tail_user_turns(path, 2, end_offset=mid_offset)
    incremental, _ = read_after(path, mid_offset)
    combined = context + incremental
    contents = [t.content for t in combined if t.role == "human"]
    assert "ctx2" in contents
    assert "new_msg" in contents


def test_read_after_no_new_content(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_turns(path, [("user", "only")])
    offset = path.stat().st_size
    turns, new_offset = read_after(path, offset)
    assert turns == []
    assert new_offset == offset


def test_read_after_missing_file(tmp_path):
    path = tmp_path / "missing.jsonl"
    turns, offset = read_after(path, 100)
    assert turns == []
    assert offset == 0
