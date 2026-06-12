"""MarkerState — terminal states for gate marker lifecycle."""

from __future__ import annotations

from enum import Enum


class MarkerState(str, Enum):
    consumed = "consumed"  # marker matched, tool blocked
    expired = "expired"  # TTL exceeded at check time
    ineligible = "ineligible"  # all rules failed eligibility/evidence/exclusion checks
    hash_mismatch = "hash_mismatch"  # marker for different prompt turn
    empty = "empty"  # zero rules in marker
    error = "error"  # exception during processing
