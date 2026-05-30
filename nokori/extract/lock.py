from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from ..config import Config
from ..utils.logging import get_logger

log = get_logger("nokori.extract.lock")


def _lock_exclusive_nb(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise BlockingIOError from e
            raise
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def acquire(cfg: Config) -> Iterator[bool]:
    """Exclusive extract lock under data_dir. Yields False if already held."""
    cfg.ensure_dirs()
    lock_path = cfg.data_dir / "extract.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            _lock_exclusive_nb(fd)
        except BlockingIOError:
            log.info("extract lock busy, skipping (%s)", lock_path)
            yield False
            return
        yield True
    finally:
        try:
            _unlock(fd)
        except OSError:
            pass
        os.close(fd)
