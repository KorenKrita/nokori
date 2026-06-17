"""Lifecycle fire/shadow evidence aggregation.

Deep module behind a small interface; consumed by transitions._gather_*
and web API readers. Concentrates the DB-access aggregation logic that
was previously inlined in transitions.py and web/api/rules.py.

Does NOT import from transitions.py (avoids circular import).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from ..db import Db
from ..events.shadow import count_shadow_evidence
from ..policy import (
    FALSE_POSITIVE_REASON_CODES,
    RECENT_EVENT_WINDOW,
)
from ..utils.time import local_days_ago

# ---------------------------------------------------------------------------
# Fire evidence aggregation (moved from transitions.py:902)
# ---------------------------------------------------------------------------


def gather_fire_evidence(db: Db, rule_id: str, *, window_days: int = 30) -> dict:
    """Count fire event labels using BOTH count window (last 10) AND time window (30 days) per spec 3.4.

    Uses BOTH count window (last 10) AND time window (30 days) per spec 3.4.
    Harmful events are counted lifetime — they do NOT decay by time alone.

    Returns the same dict shape previously produced by _aggregate_fire_evidence.
    """
    # ponytail: dict return preserved — typed FireEvidence dataclass is a future tightening
    cutoff = local_days_ago(window_days)
    recent_rows = db.fetchall(
        "SELECT posthoc_label, posthoc_reason_code, posthoc_score, session_id "
        "FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label IS NOT NULL AND posthoc_label != 'unclear' "
        "AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT ?",
        (rule_id, cutoff, RECENT_EVENT_WINDOW),
    )

    counts: dict[str, int | float | dict[str, int]] = {
        "observed_useful": 0,
        "observed_useful_strong": 0,
        "plausible_useful": 0,
        "irrelevant": 0,
        "harmful": 0,
        "total_evaluated": len(recent_rows),
    }
    reason_counts: dict[str, int] = {}
    useful_sessions: set[str] = set()
    strong_useful_sessions: set[str] = set()

    for r in recent_rows:
        label = r["posthoc_label"]
        if label in counts and label != "total_evaluated":
            counts[label] = cast(int, counts[label]) + 1
        rc = r["posthoc_reason_code"]
        if rc:
            reason_counts[rc] = reason_counts.get(rc, 0) + 1
        if label == "observed_useful" and r["session_id"]:
            useful_sessions.add(r["session_id"])
            # Spec 10.2: only strong-attribution events count toward trusted promotion.
            # NULL = legacy event (pre-attribution system) -> treated as strong for compat.
            # > 0.5 = new system confirmed strong causal attribution.
            # <= 0.5 = weak/redundant attribution -> excluded from promotion count.
            attribution_weight = r["posthoc_score"]
            if attribution_weight is None or attribution_weight > 0.5:
                counts["observed_useful_strong"] = cast(int, counts["observed_useful_strong"]) + 1
                strong_useful_sessions.add(r["session_id"])

    counts["reason_counts"] = reason_counts
    counts["distinct_observed_useful_sessions"] = len(useful_sessions)
    counts["distinct_strong_useful_sessions"] = len(strong_useful_sessions)
    counts["false_positive_rate"] = compute_false_positive_rate(counts)

    # Irrelevant in last 5 evaluated fire events
    counts["irrelevant_in_last_5"] = sum(
        1 for r in recent_rows[:5] if r["posthoc_label"] == "irrelevant"
    )

    # CRITICAL: harmful events do NOT decay by time (section 3.4).
    # Count ALL lifetime harmful events for suppression decisions.
    lifetime_harmful_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'harmful'",
        (rule_id,),
    )
    counts["lifetime_harmful"] = lifetime_harmful_row["n"] if lifetime_harmful_row else 0

    return counts


# ---------------------------------------------------------------------------
# Shadow evidence aggregation (moved from transitions.py:971)
# ---------------------------------------------------------------------------


def gather_shadow_evidence(
    db: Db,
    rule_id: str,
    rule_version: int,
    *,
    window_days: int = 30,
    shadow_type: str | None = None,
    since_iso: str | None = None,
) -> dict:
    """Count shadow labels with fingerprint dedup, distinct sessions.

    Returns the same dict shape previously produced by _aggregate_shadow_evidence.
    """
    # ponytail: dict return preserved — typed ShadowEvidence dataclass is a future tightening
    return count_shadow_evidence(
        db,
        rule_id,
        rule_version,
        window_days=window_days,
        shadow_type=shadow_type,
        since_iso=since_iso,
    )


# ---------------------------------------------------------------------------
# Candidate-specific extras (extracted from _gather_candidate_evidence)
# ---------------------------------------------------------------------------


def gather_candidate_extras(db: Db, rule_id: str, rule_version: int) -> dict:
    """Gather candidate-specific extras: synth eval, quality, miss evidence.

    Returns dict with keys: synthetic_eval_passed, admission_quality, has_miss_evidence.
    """
    # Check synthetic eval status
    synth_row = db.fetchone(
        "SELECT passed FROM rule_synthetic_evals "
        "WHERE rule_id = ? AND rule_version = ? ORDER BY created_at DESC LIMIT 1",
        (rule_id, rule_version),
    )
    synthetic_eval_passed = synth_row is not None and synth_row["passed"] == 1

    # Get admission quality from rule_reviews
    quality_row = db.fetchone(
        "SELECT scores FROM rule_reviews "
        "WHERE rule_id = ? AND decision = 'accept_active' "
        "ORDER BY created_at DESC LIMIT 1",
        (rule_id,),
    )
    admission_quality = 0.0
    if quality_row and quality_row["scores"]:
        try:
            scores = json.loads(quality_row["scores"])
            if isinstance(scores, dict):
                admission_quality = scores.get("overall_quality", 0.0)
        except (json.JSONDecodeError, TypeError):
            pass

    # Check miss evidence
    has_miss_evidence = candidate_has_miss_evidence(db, rule_id, rule_version)

    return {
        "synthetic_eval_passed": synthetic_eval_passed,
        "admission_quality": admission_quality,
        "has_miss_evidence": has_miss_evidence,
    }


# ---------------------------------------------------------------------------
# Windowed harmful count (replaces duplicated inline COUNTs)
# ---------------------------------------------------------------------------


def count_harmful_since(db: Db, rule_id: str, since_iso: str) -> int:
    """Count harmful fire events since a given ISO timestamp.

    Used for suppressed recovery (recent harmful after suppression)
    and barrier computation.
    """
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label = 'harmful' AND created_at >= ?",
        (rule_id, since_iso),
    )
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Web API reader: per-rule fire/shadow stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FireStats:
    """Per-rule fire/shadow display statistics for the web API."""

    total: int = 0
    last_at: str | None = None
    by_level: dict[str, int] | None = None
    by_label: dict[str, int] | None = None
    shadow_count: int = 0


def rule_fire_stats(db: Db, rule_id: str) -> FireStats:
    """Single source for per-rule fire/shadow display counts.

    Replaces the 5 inline queries previously in web/api/rules.py.
    """
    row = db.fetchone(
        "SELECT COUNT(*) AS cnt, MAX(created_at) AS last_at "
        "FROM rule_fire_events WHERE rule_id = ?",
        (rule_id,),
    )
    total = row["cnt"] if row else 0
    last_at = row["last_at"] if row else None

    levels_rows = db.fetchall(
        "SELECT level, COUNT(*) AS cnt FROM rule_fire_events "
        "WHERE rule_id = ? GROUP BY level",
        (rule_id,),
    )
    by_level = {r["level"]: r["cnt"] for r in levels_rows}

    posthoc_rows = db.fetchall(
        "SELECT posthoc_label, COUNT(*) AS cnt FROM rule_fire_events "
        "WHERE rule_id = ? AND posthoc_label IS NOT NULL GROUP BY posthoc_label",
        (rule_id,),
    )
    by_label = {r["posthoc_label"]: r["cnt"] for r in posthoc_rows}

    shadow_row = db.fetchone(
        "SELECT COUNT(*) AS cnt FROM rule_shadow_events WHERE rule_id = ?",
        (rule_id,),
    )
    shadow_count = shadow_row["cnt"] if shadow_row else 0

    return FireStats(
        total=total,
        last_at=last_at,
        by_level=by_level,
        by_label=by_label,
        shadow_count=shadow_count,
    )


# ---------------------------------------------------------------------------
# False-positive rate computation (shared with transitions.py)
# ---------------------------------------------------------------------------


def compute_false_positive_rate(events: dict) -> float:
    """Compute FP rate from event counts.

    fp_events = irrelevant_not_applicable + harmful_wrong_scope
                + harmful_blocked_valid_action + harmful_distracted
    denominator = total_evaluated (unclear already excluded by query)
    """
    reason_counts: dict[str, int] = events.get("reason_counts", {})
    fp_events = sum(reason_counts.get(code, 0) for code in FALSE_POSITIVE_REASON_CODES)

    total_evaluated: int = events.get("total_evaluated", 0)
    denominator = total_evaluated

    return fp_events / max(1, denominator)


# ---------------------------------------------------------------------------
# Miss evidence check (moved from transitions.py)
# ---------------------------------------------------------------------------


def candidate_has_miss_evidence(db: Db, rule_id: str, rule_version: int) -> bool:
    """Check shadow-only observed miss/user correction evidence for a candidate."""
    rows = db.fetchall(
        "SELECT decision_features FROM rule_shadow_events "
        "WHERE rule_id = ? AND shadow_rule_version = ? "
        "AND shadow_label = 'would_help_high' "
        "AND (decision_features LIKE '%agent_miss%' "
        "OR decision_features LIKE '%user_correction%') "
        "LIMIT 200",
        (rule_id, rule_version),
    )
    for row in rows:
        try:
            features = json.loads(row["decision_features"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(features, dict):
            continue
        if features.get("observed_agent_miss") is True:
            return True
        if features.get("user_correction") is True:
            return True
        if features.get("source") in {"agent_miss", "user_correction"}:
            return True
        evidence = features.get("evidence")
        if isinstance(evidence, list) and (
            "agent_miss" in evidence or "user_correction" in evidence
        ):
            return True
    return False
