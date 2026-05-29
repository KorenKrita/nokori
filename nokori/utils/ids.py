from __future__ import annotations

import uuid
from collections.abc import Iterable

MIN_SHORT_LEN = 6
MAX_SHORT_LEN = 32


def new_uuid() -> str:
    return str(uuid.uuid4())


def short_id_for(full_id: str, taken: Iterable[str]) -> str:
    """Return the shortest unique prefix of `full_id` (>= 6 chars) not in taken.

    Taken collisions force the prefix to grow one character at a time until
    distinct. If the full id is exhausted, returns the full id (always unique
    within UUID v4 space).
    """
    taken_set = set(taken)
    bare = full_id.replace("-", "")
    for length in range(MIN_SHORT_LEN, min(len(bare), MAX_SHORT_LEN) + 1):
        candidate = bare[:length]
        if candidate not in taken_set:
            return candidate
    return bare
