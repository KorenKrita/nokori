from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from threading import Lock

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(session_id)s] %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%SZ"

_configured = False
_lock = Lock()


class _SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            record.session_id = "-"
        return True


def configure(logs_dir: Path, level: str = "warn") -> None:
    global _configured
    with _lock:
        if _configured:
            return
        logs_dir.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger("nokori")
        root.setLevel(_LEVELS.get(level.lower(), logging.WARNING))
        root.propagate = False
        for h in list(root.handlers):
            root.removeHandler(h)

        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        sess_filter = _SessionFilter()

        hook_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "hook.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        hook_handler.setFormatter(formatter)
        hook_handler.addFilter(sess_filter)
        hook_handler.addFilter(_NameStartsWith(("nokori.hooks.", "nokori.gate.")))
        root.addHandler(hook_handler)

        pipeline_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "pipeline.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        pipeline_handler.setFormatter(formatter)
        pipeline_handler.addFilter(sess_filter)
        pipeline_handler.addFilter(_NameStartsWith(
            ("nokori.extract.", "nokori.lifecycle.", "nokori.llm.", "nokori.search."),
            negate_other=False,
        ))
        root.addHandler(pipeline_handler)

        _configured = True


class _NameStartsWith(logging.Filter):
    def __init__(self, prefixes: tuple[str, ...], negate_other: bool = False):
        super().__init__()
        self.prefixes = prefixes
        self.negate_other = negate_other

    def filter(self, record: logging.LogRecord) -> bool:
        match = any(record.name.startswith(p) for p in self.prefixes)
        return match if not self.negate_other else not match


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
