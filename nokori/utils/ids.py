from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable

MIN_SHORT_LEN = 6
MAX_SHORT_LEN = 32
MAX_SAFE_SESSION_ID_LEN = 120
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_SESSION_HASH_LEN = 16


def new_uuid() -> str:
    return str(uuid.uuid4())


def safe_session_id(session_id: str) -> str:
    """Return a bounded, collision-resistant filesystem path component.

    Short ASCII identifiers are preserved for readability and compatibility.
    Path-shaped, Unicode, empty, or oversized identifiers get a readable ASCII
    prefix plus a stable hash suffix so distinct raw values do not collapse to
    the same filename after sanitization.
    """
    if (
        session_id
        and len(session_id) <= MAX_SAFE_SESSION_ID_LEN
        and _SAFE_SESSION_ID_RE.fullmatch(session_id)
    ):
        return session_id

    digest = hashlib.sha256(session_id.encode("utf-8", errors="surrogatepass")).hexdigest()[
        :_SAFE_SESSION_HASH_LEN
    ]
    readable = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in session_id)
    readable = re.sub(r"_+", "_", readable).strip("_-") or "session"
    prefix_len = MAX_SAFE_SESSION_ID_LEN - _SAFE_SESSION_HASH_LEN - 1
    return f"{readable[:prefix_len]}-{digest}"


def short_id_for(full_id: str, taken: Iterable[str]) -> str:
    """Return the shortest unique prefix of `full_id` (>= 6 chars) not in taken.

    Taken collisions force the prefix to grow one character at a time until
    distinct. If the full id is exhausted, returns the full id (always unique
    within UUID v4 space).
    """
    taken_set = set(taken)
    bare = full_id.replace("-", "")

    def _collides(candidate: str) -> bool:
        if candidate in taken_set:
            return True
        for existing in taken_set:
            if candidate.startswith(existing) or existing.startswith(candidate):
                return True
        return False

    for length in range(MIN_SHORT_LEN, min(len(bare), MAX_SHORT_LEN) + 1):
        candidate = bare[:length]
        if not _collides(candidate):
            return candidate
    return bare
