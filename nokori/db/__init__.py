"""nokori.db — database access layer.

All public names are re-exported here for backward compatibility.
Callers may continue using ``from nokori.db import Db, open_db, fetch_rules``
without modification.
"""

from __future__ import annotations

from ..errors import DbError
from .connection import Db, open_db
from .queries import (
    RULE_COLUMNS,
    _delete_rule_cascade_tx,
    archive_rule,
    dumps_json,
    fetch_rule_by_short_id,
    fetch_rule_ids,
    fetch_rules,
    fetch_rules_by_short_ids,
    fetch_short_ids,
    find_rule_id_by_injection,
    find_rule_id_by_recent_injection,
    find_rule_id_injected_since,
    loads_json,
    retrieval_pool_count,
    row_to_rule,
    total_rule_count,
)
from .schema import SCHEMA_VERSION

__all__ = [
    "Db",
    "DbError",
    "RULE_COLUMNS",
    "SCHEMA_VERSION",
    "_delete_rule_cascade_tx",
    "archive_rule",
    "dumps_json",
    "fetch_rule_by_short_id",
    "fetch_rule_ids",
    "fetch_rules",
    "fetch_rules_by_short_ids",
    "fetch_short_ids",
    "find_rule_id_by_injection",
    "find_rule_id_by_recent_injection",
    "find_rule_id_injected_since",
    "loads_json",
    "open_db",
    "retrieval_pool_count",
    "row_to_rule",
    "total_rule_count",
]
