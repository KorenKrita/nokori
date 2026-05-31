"""Shared local embed weight prefetch (install / pip / CLI)."""
from __future__ import annotations

import sys
from typing import TextIO

from .config import Config


def maybe_prefetch_local_embed(
    cfg: Config | None = None,
    *,
    out: TextIO | None = None,
) -> bool:
    """Download MiniLM weights when local-embed is installed and cache is empty.

    Returns True if weights are on disk after this call (or were already).
    No-op when sentence-transformers is not installed.
    """
    from .search.embedding import (
        local_embed_package_available,
        local_model_cache_dir,
        local_model_cached,
        prefetch_local_model,
    )

    stream = out if out is not None else sys.stderr

    if not local_embed_package_available():
        return False

    cfg = cfg or Config.from_env()
    if local_model_cached(cfg):
        print(
            f"(nokori) embed weights already present under {local_model_cache_dir(cfg)}",
            file=stream,
        )
        return True

    print(
        "(nokori) prefetching local embed model (may take a few minutes)...",
        file=stream,
    )
    try:
        cache = prefetch_local_model(cfg)
    except Exception as e:
        print(f"(nokori) embed prefetch failed: {e}", file=stream)
        print("(nokori) retry later: nokori embed prefetch", file=stream)
        return False

    print(f"(nokori) embed prefetch done: {cache}", file=stream)
    return True
