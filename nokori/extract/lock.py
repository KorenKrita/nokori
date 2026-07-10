from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import Config
from ..utils import file_lock


def _path(cfg: Config) -> Path:
    return cfg.data_dir / "extract.lock"


def is_locked(cfg: Config) -> bool:
    """True when another process holds the exclusive extract lock."""
    cfg.ensure_dirs()
    return file_lock.is_locked(_path(cfg))


@contextmanager
def acquire(cfg: Config) -> Iterator[bool]:
    """Exclusive extract lock under data_dir. Yields False if already held."""
    cfg.ensure_dirs()
    with file_lock.acquire(_path(cfg), label="extract") as acquired:
        yield acquired
