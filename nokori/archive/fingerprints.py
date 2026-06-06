from __future__ import annotations

import hashlib
import re
import uuid

from ..db import Db

_STRENGTH_ORDER = ("replacement", "system", "user")


def compute_signature(
    trigger_canonical: str,
    action_instruction: str,
    domain_tags: list[str] | None = None,
) -> str:
    """Normalized hash of trigger + action + sorted tags for equivalence detection."""
    parts = [
        trigger_canonical.strip().lower(),
        action_instruction.strip().lower(),
    ]
    if domain_tags:
        parts.append(",".join(sorted(t.strip().lower() for t in domain_tags)))
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_archived_fingerprint_from_data(
    db: Db,
    rule_id: str,
    trigger_canonical: str,
    action_instruction: str,
    domain_tags: list[str] | None = None,
    strength: str = "user",
) -> str:
    """Create an archived fingerprint from raw rule data (no rule object needed).

    Used by archive_rule() when the rule row is already fetched as a dict.
    """
    signature = compute_signature(trigger_canonical, action_instruction, domain_tags)

    scope_summary = f"domain:{','.join(domain_tags)}" if domain_tags else "general"
    can_be_overridden = 1 if strength in ("user", "system") else 0

    fp_id = str(uuid.uuid4())
    now = _now_iso()

    # Atomic upsert: INSERT OR IGNORE + conditional UPDATE in one transaction.
    # Prevents TOCTOU race under concurrent calls with the same signature.
    with db.transaction() as tx:
        tx.execute(
            "INSERT OR IGNORE INTO archived_fingerprints "
            "(id, signature, scope_summary, blocked_trigger_area, blocked_action_area, "
            "archive_strength, can_be_overridden_by_changed_scope, rule_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                fp_id, signature, scope_summary,
                trigger_canonical, action_instruction,
                strength, can_be_overridden, rule_id, now,
            ),
        )
        if tx.execute("SELECT changes()").fetchone()[0] == 0:
            # Row already exists — upgrade strength if new is stronger
            existing = tx.execute(
                "SELECT id, archive_strength FROM archived_fingerprints WHERE signature = ?",
                (signature,),
            ).fetchone()
            if existing:
                existing_strength = existing["archive_strength"]
                if _STRENGTH_ORDER.index(strength) > _STRENGTH_ORDER.index(existing_strength):
                    tx.execute(
                        "UPDATE archived_fingerprints SET archive_strength = ?, "
                        "can_be_overridden_by_changed_scope = ?, created_at = ? "
                        "WHERE id = ?",
                        (strength, can_be_overridden, now, existing["id"]),
                    )
                return existing["id"]
    return fp_id


def check_fingerprint_block(
    db: Db,
    trigger_canonical: str,
    action_instruction: str,
    domain_tags: list[str] | None = None,
    stronger_evidence: str | None = None,
    synthetic_eval_passed: bool = True,
    admission_judge_cited: bool = False,
) -> dict | None:
    """Check if an archived fingerprint blocks a proposed rule.

    Returns blocking fingerprint info dict or None if not blocked.

    Args:
        stronger_evidence: Any non-empty string constitutes sufficient future
            evidence to override a system-strength archive block (when combined
            with other conditions). Replaces the former scope_change_evidence
            parameter which required specific scope-change content.

    Blocking logic (plan sections 3.5 and 11):
      user strength: blocked unless stronger_evidence provided.
      system strength: blocked but overridable with BOTH stronger_evidence
          AND synthetic_eval_passed.
      replacement strength: blocks exact duplicates only.
    """
    signature = compute_signature(trigger_canonical, action_instruction, domain_tags)

    exact_row = db.fetchone(
        "SELECT id, signature, scope_summary, blocked_trigger_area, "
        "blocked_action_area, archive_strength, "
        "can_be_overridden_by_changed_scope, rule_id, created_at "
        "FROM archived_fingerprints WHERE signature = ?",
        (signature,),
    )
    if exact_row is not None:
        return _fingerprint_decision(
            exact_row,
            stronger_evidence=stronger_evidence,
            exact_match=True,
            is_narrower_scope=False,  # exact match = same scope
            synthetic_eval_passed=synthetic_eval_passed,
            admission_judge_cited=admission_judge_cited,
        )

    row = _find_related_fingerprint(db, trigger_canonical, action_instruction)
    if row is None:
        return None
    # Related (non-exact) match: check if new rule is narrower scope
    is_narrower = _is_narrower_scope(
        trigger_canonical, action_instruction, row,
        new_domain_tags=domain_tags,
    )
    return _fingerprint_decision(
        row,
        stronger_evidence=stronger_evidence,
        exact_match=False,
        is_narrower_scope=is_narrower,
        synthetic_eval_passed=synthetic_eval_passed,
        admission_judge_cited=admission_judge_cited,
    )


def _fingerprint_decision(
    row,
    *,
    stronger_evidence: str | None,
    exact_match: bool,
    is_narrower_scope: bool = False,
    synthetic_eval_passed: bool = True,
    admission_judge_cited: bool = False,
):
    strength = row["archive_strength"]

    # Replacement blocks exact duplicates and weaker replacements only (spec section 11)
    if strength == "replacement":
        if exact_match:
            return {
                "blocked": True,
                "fingerprint_id": row["id"],
                "archive_strength": strength,
                "scope_summary": row["scope_summary"],
                "blocked_trigger_area": row["blocked_trigger_area"],
                "blocked_action_area": row["blocked_action_area"],
                "reason": "replacement_blocks_equivalent_or_weaker",
                "overridable": True,
            }
        # Non-exact fuzzy match: block if new rule is broader/weaker (not narrower)
        if not is_narrower_scope:
            return {
                "blocked": True,
                "fingerprint_id": row["id"],
                "archive_strength": strength,
                "scope_summary": row["scope_summary"],
                "blocked_trigger_area": row["blocked_trigger_area"],
                "blocked_action_area": row["blocked_action_area"],
                "reason": "replacement_blocks_weaker_replacement",
                "overridable": True,
            }
        return None

    # User archive: blocks equivalent/broader. Only NARROWER rules may proceed
    # when narrower scope + stronger evidence + admission judge cited + can_be_overridden (spec 3.5)
    if strength == "user":
        if (
            is_narrower_scope
            and stronger_evidence
            and admission_judge_cited
            and row["can_be_overridden_by_changed_scope"]
        ):
            return None
        return {
            "blocked": True,
            "fingerprint_id": row["id"],
            "archive_strength": strength,
            "scope_summary": row["scope_summary"],
            "blocked_trigger_area": row["blocked_trigger_area"],
            "blocked_action_area": row["blocked_action_area"],
            "reason": "user_archive_blocks_equivalent_or_broader",
            "overridable": bool(row["can_be_overridden_by_changed_scope"]),
        }

    # System archive: weak negative memory, overridable ONLY when BOTH
    # stronger_evidence is provided (any non-empty string) AND synthetic_eval_passed.
    if strength == "system":
        if (
            stronger_evidence
            and synthetic_eval_passed
            and row["can_be_overridden_by_changed_scope"]
        ):
            return None
        return {
            "blocked": True,
            "fingerprint_id": row["id"],
            "archive_strength": strength,
            "scope_summary": row["scope_summary"],
            "blocked_trigger_area": row["blocked_trigger_area"],
            "blocked_action_area": row["blocked_action_area"],
            "reason": "system_archive",
            "overridable": True,
        }

    return None


def _find_related_fingerprint(
    db: Db, trigger_canonical: str, action_instruction: str
):
    new_tokens = _content_tokens(f"{trigger_canonical} {action_instruction}")
    if not new_tokens:
        return None
    rows = db.fetchall(
        "SELECT id, signature, scope_summary, blocked_trigger_area, "
        "blocked_action_area, archive_strength, "
        "can_be_overridden_by_changed_scope, rule_id, created_at "
        "FROM archived_fingerprints "
        "WHERE archive_strength IN ('user','system','replacement')",
    )
    # Collect all matches that pass their per-strength threshold.
    # Return the strongest match (user > system > replacement).
    _thresholds = {"user": 0.75, "system": 0.75, "replacement": 0.85}
    candidates: list[tuple[int, float, dict]] = []
    for row in rows:
        old_tokens = _content_tokens(
            f"{row['blocked_trigger_area']} {row['blocked_action_area']}"
        )
        if not old_tokens:
            continue
        overlap = len(new_tokens & old_tokens) / len(old_tokens)
        containment = len(new_tokens & old_tokens) / len(new_tokens)
        score = max(overlap, containment)
        threshold = _thresholds.get(row["archive_strength"], 0.75)
        if score >= threshold:
            priority = _STRENGTH_ORDER.index(row["archive_strength"])
            candidates.append((priority, score, row))

    if not candidates:
        return None
    # Highest priority (user=2 > system=1 > replacement=0), then highest score
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def _is_narrower_scope(
    trigger_canonical: str,
    action_instruction: str,
    fingerprint_row,
    *,
    new_domain_tags: list[str] | None = None,
) -> bool:
    """Determine if the new rule has narrower scope than the archived fingerprint.

    Narrower = the new rule covers less ground than the fingerprint. Checked via:
    1. Token containment: new tokens are mostly a subset of old tokens
    2. Scope specificity: new rule has more domain/path constraints than old
    """
    new_tokens = _content_tokens(f"{trigger_canonical} {action_instruction}")
    old_tokens = _content_tokens(
        f"{fingerprint_row['blocked_trigger_area']} {fingerprint_row['blocked_action_area']}"
    )
    if not new_tokens or not old_tokens:
        return False
    new_in_old = len(new_tokens & old_tokens) / len(new_tokens) if new_tokens else 0
    old_in_new = len(new_tokens & old_tokens) / len(old_tokens) if old_tokens else 0

    # Structural narrowness: new has domain_tags that old's scope_summary lacks
    scope_summary = fingerprint_row.get("scope_summary") or ""
    has_structural_narrowing = (
        new_domain_tags
        and len(new_domain_tags) > 0
        and scope_summary == "general"
    )

    token_narrower = new_in_old >= 0.80 and old_in_new < 0.70
    return token_narrower or bool(has_structural_narrowing)


def _content_tokens(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "before", "after", "rule", "use"}
    return {t for t in re.findall(r"[a-z0-9_+-]{3,}", text.lower()) if t not in stop}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
