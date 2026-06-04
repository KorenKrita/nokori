"""Cross-project promotion logic.

.. deprecated::
    This module is superseded by :mod:`nokori.lifecycle.transitions` which
    handles all status transitions via the autonomous rule quality flywheel.
    Functions are kept as no-ops / thin wrappers for backward compatibility.
"""

from __future__ import annotations

from ..db import Db, loads_json
from ..utils.logging import get_logger

log = get_logger("nokori.lifecycle.promotion")

# Kept for backward compat (imported by web/api/lifecycle.py, commands/status.py)
CROSS_PROJECT_PROMOTE_THRESHOLD = 3


def unique_promotion_project_ids(promotion_evidence: str | list | None) -> list[str]:
    """Distinct other-project ids recorded for global promotion (stable append order).

    Retained for read-only display in status/web endpoints.
    """
    if promotion_evidence is None:
        raw: list = []
    elif isinstance(promotion_evidence, str):
        raw = loads_json(promotion_evidence, [])
    else:
        raw = promotion_evidence
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in raw:
        pid = entry.get("project_id")
        if pid and pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def record_shadow_hit(db: Db, rule_id: str, current_project_id: str | None) -> bool:
    """No-op. Shadow hits are now recorded via lifecycle.transitions.

    .. deprecated::
        Use :func:`nokori.lifecycle.transitions.evaluate_transitions` instead.
        Shadow evidence is tracked by :mod:`nokori.events.shadow`.
    """
    return False
