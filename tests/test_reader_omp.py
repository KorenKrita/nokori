import json

from nokori.extract.reader import read, read_after


def _write_jsonl(path, entries):
    path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _append_jsonl(path, entries):
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(entry) for entry in entries) + "\n")


def _omp_message(role, *, content=None, **extra):
    message = {"role": role, **extra}
    if content is not None:
        message["content"] = content
    return {"type": "message", "message": message}


USER_TEXT = _omp_message(
    "user",
    content=[{"type": "text", "text": "find TODOs"}],
)
ASSISTANT_TEXT = _omp_message(
    "assistant",
    content=[{"type": "text", "text": "I'll inspect the repo."}],
)
ASSISTANT_TOOL_CALL = _omp_message(
    "assistant",
    content=[
        {"type": "toolCall", "name": "read", "arguments": {"path": "src/app.py"}}
    ],
)
SUCCESS_TOOL_RESULT = _omp_message(
    "toolResult",
    toolName="read",
    content="file contents",
    isError=False,
)
ERROR_TOOL_RESULT = _omp_message(
    "toolResult",
    toolName="read",
    content="ENOENT: missing",
    isError=True,
)


def test_read_parses_omp_jsonl_messages(tmp_path):
    transcript = tmp_path / "omp.jsonl"
    _write_jsonl(
        transcript,
        [
            USER_TEXT,
            ASSISTANT_TEXT,
            ASSISTANT_TOOL_CALL,
            SUCCESS_TOOL_RESULT,
            ERROR_TOOL_RESULT,
        ],
    )

    turns = read(transcript)

    assert [t.role for t in turns] == [
        "human",
        "assistant",
        "tool_use",
        "tool_result",
        "tool_result",
    ]
    assert turns[0].content == "find TODOs"
    assert turns[1].content == "I'll inspect the repo."
    assert turns[2].tool_name == "read"
    assert turns[2].input_summary == '{"path": "src/app.py"}'
    assert turns[3].tool_name == "read"
    assert turns[3].content == "file contents"
    assert turns[3].is_error is False
    assert turns[4].tool_name == "read"
    assert turns[4].content == ""
    assert turns[4].is_error is True
    assert turns[4].error_line == "ENOENT: missing"


def test_read_after_parses_incremental_omp_jsonl_messages(tmp_path):
    transcript = tmp_path / "omp.jsonl"
    _write_jsonl(transcript, [USER_TEXT, ASSISTANT_TEXT])
    offset = transcript.stat().st_size

    _append_jsonl(
        transcript,
        [ASSISTANT_TOOL_CALL, SUCCESS_TOOL_RESULT, ERROR_TOOL_RESULT],
    )

    turns, new_offset = read_after(transcript, offset)

    assert [t.role for t in turns] == ["tool_use", "tool_result", "tool_result"]
    assert turns[0].tool_name == "read"
    assert turns[0].input_summary == '{"path": "src/app.py"}'
    assert turns[1].content == "file contents"
    assert turns[1].tool_name == "read"
    assert turns[2].is_error is True
    assert turns[2].error_line == "ENOENT: missing"
    assert turns[2].tool_name == "read"
    assert new_offset == transcript.stat().st_size


def test_read_uses_large_line_fast_path_for_omp_messages(tmp_path):
    transcript = tmp_path / "omp-large.jsonl"
    huge_tool_call = _omp_message(
        "assistant",
        content=[
            {
                "type": "toolCall",
                "name": "write",
                "arguments": {"path": "large.txt", "content": "x" * 6000},
            }
        ],
    )
    huge_error = _omp_message(
        "toolResult",
        toolName="write",
        content="permission denied: " + "x" * 6000,
        isError=True,
    )
    _write_jsonl(transcript, [huge_tool_call, huge_error])

    turns = read(transcript)

    assert [turn.role for turn in turns] == ["tool_use", "tool_result"]
    assert turns[0].tool_name == "write"
    assert turns[0].input_summary == "[large input truncated]"
    assert turns[1].tool_name == "write"
    assert turns[1].is_error is True
    assert turns[1].error_line.startswith("permission denied")
