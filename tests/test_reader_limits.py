import pytest

from nokori.constants import MAX_TRANSCRIPT_BYTES
from nokori.errors import NokoriError
from nokori.extract.reader import read


def test_read_rejects_oversized_transcript(tmp_path):
    path = tmp_path / "big.jsonl"
    path.write_bytes(b"x" * (MAX_TRANSCRIPT_BYTES + 1))
    with pytest.raises(NokoriError, match="too large"):
        read(path)
