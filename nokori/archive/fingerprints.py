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


def check_fingerprint_block(
    db: Db,
    trigger_canonical: str,
    action_instruction: str,
    domain_tags: list[str] | None = None,
    scope_change_evidence: str | None = None,
) -> dict | None:
    """Check if an archived fingerprint blocks a proposed rule.

    Returns blocking fingerprint info dict or None if not blocked.

    Blocking logic (plan sections 3.5 and 11):
      user strength: blocked unless scope_change_evidence provided.
      system strength: blocked but overridable with stronger evidence.
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
            scope_change_evidence=scope_change_evidence,
            exact_match=True,
        )

    row = _find_related_fingerprint(db, trigger_canonical, action_instruction)
    if row is None:
        return None
    return _fingerprint_decision(
        row,
        scope_change_evidence=scope_change_evidence,
        exact_match=False,
    )


def _fingerprint_decision(row, *, scope_change_evidence: str | None, exact_match: bool):
    strength = row["archive_strength"]
    if strength == "replacement" and not exact_match:
        return None

    if scope_change_evidence and row["can_be_overridden_by_changed_scope"]:
        return None

    if strength == "user" and not scope_change_evidence:
        return {
            "blocked": True,
            "fingerprint_id": row["id"],
            "archive_strength": strength,
            "scope_summary": row["scope_summary"],
            "blocked_trigger_area": row["blocked_trigger_area"],
            "blocked_action_area": row["blocked_action_area"],
            "reason": "user_archive_no_scope_change",
            "overridable": bool(row["can_be_overridden_by_changed_scope"]),
        }

    if strength == "system":
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

    if strength == "replacement":
        return {
            "blocked": True,
            "fingerprint_id": row["id"],
            "archive_strength": strength,
            "scope_summary": row["scope_summary"],
            "blocked_trigger_area": row["blocked_trigger_area"],
            "blocked_action_area": row["blocked_action_area"],
            "reason": "replacement_exact_duplicate",
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
        "WHERE archive_strength IN ('user','system')",
    )
    best = None
    best_score = 0.0
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
    return best if best_score >= 0.75 else None


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
