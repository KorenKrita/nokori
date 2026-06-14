"""Shared constants for cold-path pipeline modules."""

PIPELINE_VERSION: str = "1.0.0"

DESTRUCTIVE_MERGE_OPS: frozenset[str] = frozenset(
    (
        "replace_existing",
        "suppress_existing",
        "archive_existing",
    )
)

_MAX_SPLIT_DEPTH: int = 3
