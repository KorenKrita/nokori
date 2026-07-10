from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .logging import get_logger

log = get_logger("nokori.utils.file_lock")


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

        with suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


def is_locked(path: Path) -> bool:
    """Return whether another process holds the exclusive lock at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            _lock_exclusive_nb(fd)
        except BlockingIOError:
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)


@contextmanager
def acquire(path: Path, *, label: str = "file") -> Iterator[bool]:
    """Try to hold an exclusive process lock, yielding False when busy."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            _lock_exclusive_nb(fd)
            acquired = True
        except BlockingIOError:
            log.info("%s lock busy, skipping (%s)", label, path)
            yield False
            return
        try:
            if os.path.getsize(str(path)) == 0:
                os.write(fd, b"1")
        except OSError:
            pass
        yield True
    finally:
        if acquired:
            with suppress(OSError):
                _unlock(fd)
        os.close(fd)
