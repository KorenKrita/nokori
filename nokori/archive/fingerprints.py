from __future__ import annotations

import hashlib
import re
import uuid

from ..db import Db, loads_json

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


def create_archived_fingerprint(
    db: Db,
    rule,
    strength: str = "system",
) -> str:
    """Create an archived fingerprint from a rule.

    Returns the fingerprint id.

    Strength semantics (from plan section 11):
      user: blocks equivalent or broader future rules by default.
      system: weak negative memory, overridable with stronger evidence.
      replacement: blocks exact duplicates only.
    """
    domain_tags = (
        rule.domain_tags
        if isinstance(rule.domain_tags, list)
        else loads_json(rule.domain_tags if isinstance(rule.domain_tags, str) else None, [])
    )

    signature = compute_signature(
        rule.trigger_canonical,
        rule.action_instruction,
        domain_tags,
    )

    scope_summary = _build_scope_summary(rule, domain_tags)
    blocked_trigger_area = rule.trigger_canonical
    blocked_action_area = rule.action_instruction

    # User/system vetoes can be overridden only with explicit changed-scope
    # evidence; replacement fingerprints block exact duplicates only.
    can_be_overridden = 1 if strength in ("user", "system") else 0

    fp_id = str(uuid.uuid4())
    now = _now_iso()

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO archived_fingerprints "
            "(id, signature, scope_summary, blocked_trigger_area, blocked_action_area, "
            "archive_strength, can_be_overridden_by_changed_scope, rule_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                fp_id,
                signature,
                scope_summary,
                blocked_trigger_area,
                blocked_action_area,
                strength,
                can_be_overridden,
                rule.id,
                now,
            ),
        )
    return fp_id


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

    # Upsert: update strength if existing fingerprint is weaker
    existing = db.fetchone(
        "SELECT id, archive_strength FROM archived_fingerprints WHERE signature = ?",
        (signature,),
    )
    if existing:
        existing_strength = existing["archive_strength"]
        if _STRENGTH_ORDER.index(strength) > _STRENGTH_ORDER.index(existing_strength):
            with db.transaction() as tx:
                tx.execute(
                    "UPDATE archived_fingerprints SET archive_strength = ?, "
                    "can_be_overridden_by_changed_scope = ?, created_at = ? "
                    "WHERE id = ?",
                    (strength, can_be_overridden, now, existing["id"]),
                )
            return existing["id"]
        return existing["id"]

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO archived_fingerprints "
            "(id, signature, scope_summary, blocked_trigger_area, blocked_action_area, "
            "archive_strength, can_be_overridden_by_changed_scope, rule_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                fp_id, signature, scope_summary,
                trigger_canonical, action_instruction,
                strength, can_be_overridden, rule_id, now,
            ),
        )
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
        trigger_canonical, action_instruction, row
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
        if not exact_match:
            return None
        # exact_match = True means equivalent or weaker -> block
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

    # User archive: blocks equivalent/broader. Only NARROWER rules may proceed
    # when stronger evidence is provided (any non-empty string) AND
    # (admission_judge_cited or synthetic_eval_passed) AND can_be_overridden (spec 3.5)
    if strength == "user":
        if (
            is_narrower_scope
            and stronger_evidence
            and (admission_judge_cited or synthetic_eval_passed)
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
    best = None
    best_score = 0.0
    best_strength = None
    for row in rows:
        old_tokens = _content_tokens(
            f"{row['blocked_trigger_area']} {row['blocked_action_area']}"
        )
        if not old_tokens:
            continue
        overlap = len(new_tokens & old_tokens) / len(old_tokens)
        containment = len(new_tokens & old_tokens) / len(new_tokens)
        score = max(overlap, containment)
        if score > best_score:
            best = row
            best_score = score
            best_strength = row["archive_strength"]
    # Replacement fingerprints require a stricter threshold (0.85) for fuzzy match
    # to approximate "weaker replacement" detection; user/system use 0.75.
    threshold = 0.85 if best_strength == "replacement" else 0.75
    return best if best_score >= threshold else None


def _is_narrower_scope(
    trigger_canonical: str,
    action_instruction: str,
    fingerprint_row,
) -> bool:
    """Determine if the new rule has narrower scope than the archived fingerprint.

    Narrower = the new rule's tokens are a STRICT SUBSET of the fingerprint's tokens,
    meaning it covers less ground.
    """
    new_tokens = _content_tokens(f"{trigger_canonical} {action_instruction}")
    old_tokens = _content_tokens(
        f"{fingerprint_row['blocked_trigger_area']} {fingerprint_row['blocked_action_area']}"
    )
    if not new_tokens or not old_tokens:
        return False
    # Narrower = new tokens mostly contained in old (subset) AND old is NOT
    # contained in new (new doesn't cover everything old did).
    new_in_old = len(new_tokens & old_tokens) / len(new_tokens) if new_tokens else 0
    old_in_new = len(new_tokens & old_tokens) / len(old_tokens) if old_tokens else 0
    return new_in_old >= 0.80 and old_in_new < 0.70


def _content_tokens(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "before", "after", "rule", "use"}
    return {t for t in re.findall(r"[a-z0-9_+-]{3,}", text.lower()) if t not in stop}


def is_narrower_scope(
    new_trigger: str,
    existing_trigger: str,
    new_tags: list[str] | None,
    existing_tags: list[str] | None,
) -> bool:
    """Simple heuristic: new trigger is longer/more specific, new tags are subset.

    A narrower scope means the new rule applies to fewer situations than the
    existing archived fingerprint, so it may be admissible despite the block.
    """
    new_tags = new_tags or []
    existing_tags = existing_tags or []

    # Longer trigger text implies more specificity
    trigger_narrower = len(new_trigger.strip()) > len(existing_trigger.strip())

    # New tags being a proper subset of existing tags implies narrower scope
    tags_narrower = (
        set(new_tags).issubset(set(existing_tags)) and len(new_tags) < len(existing_tags)
    ) if existing_tags else False

    return trigger_narrower or tags_narrower


def upgrade_fingerprint_strength(
    db: Db,
    fingerprint_id: str,
    new_strength: str,
) -> None:
    """Upgrade archive_strength. Strength order: user > system > replacement."""
    row = db.fetchone(
        "SELECT archive_strength FROM archived_fingerprints WHERE id = ?",
        (fingerprint_id,),
    )
    if row is None:
        return

    current = row["archive_strength"]
    current_rank = _STRENGTH_ORDER.index(current) if current in _STRENGTH_ORDER else -1
    new_rank = _STRENGTH_ORDER.index(new_strength) if new_strength in _STRENGTH_ORDER else -1

    if new_rank <= current_rank:
        return

    with db.transaction() as tx:
        tx.execute(
            "UPDATE archived_fingerprints SET archive_strength = ? WHERE id = ?",
            (new_strength, fingerprint_id),
        )


def get_fingerprints_for_rule(db: Db, rule_id: str) -> list[dict]:
    """Fetch archived fingerprints associated with a rule."""
    rows = db.fetchall(
        "SELECT id, signature, scope_summary, blocked_trigger_area, "
        "blocked_action_area, archive_strength, "
        "can_be_overridden_by_changed_scope, rule_id, created_at "
        "FROM archived_fingerprints WHERE rule_id = ?",
        (rule_id,),
    )
    return [
        {
            "id": r["id"],
            "signature": r["signature"],
            "scope_summary": r["scope_summary"],
            "blocked_trigger_area": r["blocked_trigger_area"],
            "blocked_action_area": r["blocked_action_area"],
            "archive_strength": r["archive_strength"],
            "can_be_overridden_by_changed_scope": bool(r["can_be_overridden_by_changed_scope"]),
            "rule_id": r["rule_id"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _build_scope_summary(rule, domain_tags: list[str]) -> str:
    """Build a human-readable scope summary from rule fields."""
    parts = []
    if domain_tags:
        parts.append(f"domains: {', '.join(domain_tags)}")
    tool_tags = (
        rule.tool_tags
        if isinstance(rule.tool_tags, list)
        else loads_json(rule.tool_tags if isinstance(rule.tool_tags, str) else None, [])
    )
    if tool_tags:
        parts.append(f"tools: {', '.join(tool_tags)}")
    path_patterns = (
        rule.path_patterns
        if isinstance(rule.path_patterns, list)
        else loads_json(
            rule.path_patterns if isinstance(rule.path_patterns, str) else None, []
        )
    )
    if path_patterns:
        parts.append(f"paths: {', '.join(path_patterns)}")
    return "; ".join(parts) if parts else ""


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
