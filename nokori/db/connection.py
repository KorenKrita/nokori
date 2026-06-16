from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..errors import DbError
from .schema import _migrate, _read_version


class Db:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._in_tx = False

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._in_tx:
            raise DbError("nested database transaction")
        self._in_tx = True
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._in_tx = False

    def schema_version(self) -> int:
        return _read_version(self.conn)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        cur = self.conn.execute(sql, params)
        return cur.fetchone()  # type: ignore[no-any-return]

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        return cur.fetchall()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def open_db(path: Path) -> Db:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            conn = _connect(path)
            try:
                _migrate(conn)
            except Exception:
                conn.close()
                raise
            return Db(conn)
        except (sqlite3.OperationalError, DbError) as e:
            err_msg = str(e).lower()
            if "locked" not in err_msg and "busy" not in err_msg:
                raise
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    remediation: str
    if last_err and "locked" in str(last_err).lower():
        remediation = "Check for orphaned nokori processes: ps aux | grep nokori"
    else:
        remediation = (
            "Ensure ~/.nokori exists with mode 700: mkdir -p ~/.nokori && chmod 700 ~/.nokori"
        )
    raise DbError(
        f"failed to open db at {path}: {last_err}",
        remediation=remediation,
    )
