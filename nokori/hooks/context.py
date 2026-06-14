from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..config import Config
from ..db import Db, open_db
from ..events.observability import write_event
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.context")


class ErrorCategory(Enum):
    RETRIABLE = "retriable"
    TERMINAL = "terminal"
    DEGRADED = "degraded"


@dataclass
class SubsystemError:
    subsystem: str
    category: ErrorCategory
    message: str
    exception: BaseException | None = None


class HotPathContext:
    """Session-scoped context for hot path hooks.

    Owns lazy DB connection, error collection, and observability event writing.
    Use as a context manager: ``with HotPathContext(...) as ctx:``
    """

    __slots__ = (
        "cfg",
        "host",
        "session_id",
        "payload",
        "_db",
        "_db_open_attempted",
        "_db_unavailable",
        "errors",
    )

    def __init__(
        self,
        payload: dict,
        cfg: Config,
        *,
        host: Host,
        session_id: str | None = None,
    ) -> None:
        self.payload = payload
        self.cfg = cfg
        self.host = host
        self.session_id = session_id or effective_session_id(payload)
        self._db: Db | None = None
        self._db_open_attempted = False
        self._db_unavailable = False
        self.errors: list[SubsystemError] = []

    @property
    def db(self) -> Db | None:
        if self._db_open_attempted:
            return self._db
        self._db_open_attempted = True
        try:
            self._db = open_db(self.cfg.db_path)
        except Exception as e:
            self._db_unavailable = True
            self.add_error("db", ErrorCategory.DEGRADED, str(e), e)
            log.warning("HotPathContext db open failed session=%s: %s", self.session_id, e)
        return self._db

    @property
    def db_unavailable(self) -> bool:
        return self._db_unavailable

    def add_error(
        self,
        subsystem: str,
        category: ErrorCategory,
        message: str,
        exception: BaseException | None = None,
    ) -> None:
        self.errors.append(
            SubsystemError(
                subsystem=subsystem,
                category=category,
                message=message,
                exception=exception,
            )
        )

    def record_event(
        self,
        source: str,
        outcome: str,
        *,
        prompt_snippet: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str | None:
        db = self.db
        if db is None:
            return None
        try:
            result = write_event(
                db,
                source=source,
                outcome=outcome,
                session_id=self.session_id,
                prompt_snippet=prompt_snippet,
                details=details,
            )
        except Exception as e:
            # Defensive: write_event's contract is never-raise, but guard against future changes
            log.warning("record_event failed source=%s: %s", source, e)
            self.add_error("observability", ErrorCategory.DEGRADED, str(e), e)
            return None
        return result

    def __enter__(self) -> HotPathContext:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._db is not None:
            try:
                self._db.close()
            except Exception as e:
                log.debug("HotPathContext db close failed session=%s: %s", self.session_id, e)
                self.add_error("db_close", ErrorCategory.DEGRADED, str(e), e)
            # _db_open_attempted stays True: post-exit ctx.db returns None
            self._db = None
        return False
