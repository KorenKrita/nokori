from __future__ import annotations

from collections.abc import Sequence

DEFAULT_BATCH_SIZE = 900


def batched(items: Sequence[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[str]]:
    if not items:
        return []
    return [list(items[i : i + batch_size]) for i in range(0, len(items), batch_size)]
