from nokori.utils.ids import (
    MAX_SAFE_SESSION_ID_LEN,
    MIN_SHORT_LEN,
    new_uuid,
    safe_session_id,
    short_id_for,
)


def test_new_uuid_unique():
    assert new_uuid() != new_uuid()


def test_safe_session_id_preserves_short_ascii_id():
    assert safe_session_id("session-123_abc") == "session-123_abc"


def test_safe_session_id_distinguishes_sanitization_collisions():
    slash = safe_session_id("a/b")
    question = safe_session_id("a?b")

    assert slash != question
    assert slash.startswith("a_b-")
    assert question.startswith("a_b-")


def test_safe_session_id_bounds_path_shaped_and_unicode_ids():
    raw = "/Users/测试/.omp/agent/sessions/" + "deep/" * 100 + "session.jsonl"
    safe = safe_session_id(raw)

    assert safe == safe_session_id(raw)
    assert 0 < len(safe.encode("ascii")) <= MAX_SAFE_SESSION_ID_LEN
    assert all(c.isascii() and (c.isalnum() or c in "-_") for c in safe)


def test_safe_session_id_never_returns_empty():
    assert safe_session_id("")
    assert safe_session_id("///")


def test_short_id_default_length():
    full = "abcdef12-3456-7890-abcd-ef1234567890"
    sid = short_id_for(full, taken=set())
    assert len(sid) == MIN_SHORT_LEN
    assert sid == full.replace("-", "")[:MIN_SHORT_LEN]


def test_short_id_grows_on_collision():
    full = "abcdef12-3456-7890-abcd-ef1234567890"
    taken = {"abcdef"}
    sid = short_id_for(full, taken=taken)
    assert sid != "abcdef"
    assert sid.startswith("abcdef")
