"""Deterministic merge policy for the autonomous rule quality flywheel.

Implements section 8.4 of the flywheel plan: the merge planner proposes,
deterministic policy disposes.
"""

from __future__ import annotations

from dataclasses import dataclass

from nokori.db import Db
from nokori.policy import MergeOperation
from nokori.utils.logging import get_logger

log = get_logger("nokori.merge.policy")


# ---------------------------------------------------------------------------
# MergeDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeDecision:
    """Immutable result of deterministic merge policy evaluation."""

    operation: MergeOperation
    target_rule_id: str | None
    reason: str
    requires_synthetic_reeval: bool
    lineage_record: dict | None


# ---------------------------------------------------------------------------
# Merge confidence threshold
# ---------------------------------------------------------------------------

_LOW_CONFIDENCE_THRESHOLD: float = 0.65


# ---------------------------------------------------------------------------
# apply_merge_policy
# ---------------------------------------------------------------------------


def apply_merge_policy(
    planner_output: dict,
    existing_rule: dict | None,
    new_rule_data: dict,
) -> MergeDecision:
    """Apply deterministic merge policy from section 8.4.

    The planner (LLM) proposes relation_shape, safety, quality_winner, and
    operation. This function enforces hard rules that override or confirm
    the proposal.

    Args:
        planner_output: Dict with keys relation_shape, new_rule_safety,
            operation_safety, quality_winner, operation, confidence, reason.
        existing_rule: Dict representation of the existing rule (or None
            when no merge neighbor is relevant).
        new_rule_data: Dict representation of the proposed new rule.

    Returns:
        A frozen MergeDecision.
    """
    relation: str = planner_output.get("relation_shape", "unrelated")
    new_safety: str = planner_output.get("new_rule_safety", "uncertain")
    op_safety: str = planner_output.get("operation_safety", "uncertain")
    quality_winner: str = planner_output.get("quality_winner", "neither")
    confidence: float = float(planner_output.get("confidence", 0.0))
    planner_reason: str = planner_output.get("reason", "")

    target_id: str | None = (
        existing_rule.get("id") if existing_rule else None
    )

    # --- reject_new gates (checked first) ---

    # Archived fingerprint blocks new rule.
    if new_rule_data.get("archived_fingerprint_conflict"):
        return MergeDecision(
            operation="reject_new",
            target_rule_id=target_id,
            reason="archived fingerprint blocks new rule",
            requires_synthetic_reeval=False,
            lineage_record=None,
        )

    # Unsafe new rule.
    if new_safety == "unsafe":
        return MergeDecision(
            operation="reject_new",
            target_rule_id=target_id,
            reason="new_rule_safety is unsafe",
            requires_synthetic_reeval=False,
            lineage_record=None,
        )

    # --- keep_both gates ---

    if relation == "complementary":
        return _keep_both(target_id, "complementary relation")

    if relation == "overlap" and quality_winner == "neither":
        return _keep_both(target_id, "overlap with neither rule dominating")

    if relation == "unrelated":
        return _keep_both(target_id, "unrelated rules")

    # Low confidence on a destructive relation.
    _DESTRUCTIVE_RELATIONS: frozenset[str] = frozenset(
        ("equivalent", "obsolete", "new_broader", "new_narrower", "contradiction")
    )
    if confidence < _LOW_CONFIDENCE_THRESHOLD and relation in _DESTRUCTIVE_RELATIONS:
        return MergeDecision(
            operation="reject_new",
            target_rule_id=target_id,
            reason=f"confidence {confidence:.2f} below threshold for relation {relation}",
            requires_synthetic_reeval=False,
            lineage_record=None,
        )

    if op_safety == "unsafe" and new_safety != "unsafe":
        return _keep_both(
            target_id,
            "operation_safety unsafe but new rule itself is safe; keeping both",
        )

    # --- operations requiring an existing rule ---

    if existing_rule is None:
        # No existing neighbor -- keep_both is the only safe fallback.
        return _keep_both(target_id, "no existing rule for merge target")

    existing_status: str = existing_rule.get("status", "candidate")
    existing_trusted: bool = existing_status == "trusted"

    # --- update_existing_fields ---

    if (
        relation in ("equivalent", "new_narrower")
        and op_safety == "safe"
        and existing_status in ("active", "trusted")
        and _action_semantics_unchanged(existing_rule, new_rule_data)
        and _new_evidence_improves(existing_rule, new_rule_data)
    ):
        return MergeDecision(
            operation="update_existing_fields",
            target_rule_id=target_id,
            reason="safe field-level update; action unchanged; new evidence improves",
            requires_synthetic_reeval=False,
            lineage_record=_lineage(target_id, "update_existing_fields", planner_reason),
        )

    # --- merge_into_existing ---

    if (
        relation == "equivalent"
        and op_safety == "safe"
        and quality_winner in ("both", "existing")
    ):
        return MergeDecision(
            operation="merge_into_existing",
            target_rule_id=target_id,
            reason="equivalent rule; quality winner favors existing or both",
            requires_synthetic_reeval=False,
            lineage_record=_lineage(target_id, "merge_into_existing", planner_reason),
        )

    # --- replace_existing ---

    if (
        existing_status in ("candidate", "active")
        and op_safety == "safe"
        and relation in ("equivalent", "obsolete")
        and quality_winner == "new"
    ):
        return MergeDecision(
            operation="replace_existing",
            target_rule_id=target_id,
            reason="new rule is quality winner; existing is candidate/active",
            requires_synthetic_reeval=True,
            lineage_record=_lineage(target_id, "replace_existing", planner_reason),
        )

    # --- new_broader may replace narrower (spec section 8.3) ---

    if (
        existing_status in ("candidate", "active")
        and op_safety == "safe"
        and relation == "new_broader"
        and quality_winner == "new"
    ):
        return MergeDecision(
            operation="replace_existing",
            target_rule_id=target_id,
            reason="broader rule replaces narrower; transcript evidence supports broader scope",
            requires_synthetic_reeval=True,
            lineage_record=_lineage(target_id, "replace_existing", planner_reason),
        )

    # Trusted replacement requires higher bar.
    if (
        existing_trusted
        and op_safety == "safe"
        and relation in ("equivalent", "obsolete")
        and quality_winner == "new"
        and check_trusted_replacement(existing_rule, new_rule_data, planner_output)
    ):
        return MergeDecision(
            operation="replace_existing",
            target_rule_id=target_id,
            reason="trusted replacement criteria met",
            requires_synthetic_reeval=True,
            lineage_record=_lineage(target_id, "replace_existing", planner_reason),
        )

    # --- suppress_existing ---

    if (
        op_safety == "safe"
        and relation == "contradiction"
        and quality_winner == "new"
        and _existing_has_weak_history(existing_rule)
    ):
        return MergeDecision(
            operation="suppress_existing",
            target_rule_id=target_id,
            reason="contradiction resolved for new; existing has weak history",
            requires_synthetic_reeval=True,
            lineage_record=_lineage(target_id, "suppress_existing", planner_reason),
        )

    # --- archive_existing ---

    if (
        op_safety == "safe"
        and _is_system_created_harmful_or_obsolete(existing_rule)
        and not _user_archived_opposite(existing_rule, new_rule_data)
    ):
        return MergeDecision(
            operation="archive_existing",
            target_rule_id=target_id,
            reason="existing is system-created harmful/obsolete; user did not archive opposite",
            requires_synthetic_reeval=True,
            lineage_record=_lineage(target_id, "archive_existing", planner_reason),
        )

    # --- split_required ---

    if relation == "split_required":
        return MergeDecision(
            operation="split_required",
            target_rule_id=target_id,
            reason="multiple independent triggers/actions require split",
            requires_synthetic_reeval=False,
            lineage_record=None,
        )

    # --- fallback: keep_both ---

    return _keep_both(target_id, f"no policy rule matched; relation={relation}")


# ---------------------------------------------------------------------------
# check_trusted_replacement
# ---------------------------------------------------------------------------


def check_trusted_replacement(
    existing_rule: dict,
    new_rule_data: dict,
    planner_output: dict,
) -> bool:
    """Higher bar for replacing trusted rules (section 8.4).

    All conditions must be true:
    - relation_shape in [equivalent, obsolete]
    - quality_winner = new
    - new evidence_support >= existing evidence_support
    - synthetic eval strictly improves near-miss behavior
    - existing observed usefulness is weak or stale
    """
    relation = planner_output.get("relation_shape", "")
    if relation not in ("equivalent", "obsolete"):
        return False

    if planner_output.get("quality_winner") != "new":
        return False

    new_evidence = float(new_rule_data.get("evidence_support_score", 0.0))
    existing_evidence = float(existing_rule.get("evidence_support_score", 0.0))
    if new_evidence < existing_evidence:
        return False

    # Synthetic must strictly improve (caller sets this from eval results).
    if not new_rule_data.get("synthetic_strictly_improves", False):
        return False

    # Existing usefulness must be weak or stale.
    if not _existing_usefulness_weak_or_stale(existing_rule):
        return False

    return True


# ---------------------------------------------------------------------------
# record_lineage
# ---------------------------------------------------------------------------


def record_lineage(
    db: Db,
    old_rule_id: str | None,
    new_rule_id: str | None,
    operation: str,
    reason: str,
) -> None:
    """Insert a record into rule_lineage table."""
    from datetime import datetime

    from nokori.utils.time import now_iso
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rule_lineage (old_rule_id, new_rule_id, operation, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (old_rule_id, new_rule_id, operation, reason, now),
        )


# ---------------------------------------------------------------------------
# find_merge_neighbors
# ---------------------------------------------------------------------------


def find_merge_neighbors(
    db: Db,
    rule_data: dict,
    limit: int = 10,
    project_id: str | None = None,
) -> list[dict]:
    """Broad recall for merge neighbor retrieval (section 8.1).

    Retrieves candidates via:
    - BM25 on trigger_canonical tokens
    - Trigger variant phrase overlap
    - Search terms overlap
    - Same domain/tag
    - Same action intent keywords

    Returns up to `limit` existing rules as dicts, ordered by relevance.
    """
    from nokori.db import loads_json, RULE_COLUMNS

    def _variant_texts(value) -> set[str]:
        raw_variants = loads_json(value, []) if isinstance(value, str) else value
        if not isinstance(raw_variants, list):
            return set()

        texts: set[str] = set()
        for variant in raw_variants:
            if isinstance(variant, str):
                text = variant
            elif isinstance(variant, dict):
                text = str(variant.get("text") or "")
            else:
                text = ""
            if text.strip():
                texts.add(text.strip().lower())
        return texts

    candidates: dict[str, tuple[dict, float]] = {}

    trigger = rule_data.get("trigger_canonical", "")
    trigger_tokens = _tokenize(trigger)
    variant_texts = _variant_texts(
        rule_data.get("variants") or rule_data.get("trigger_variants", [])
    )
    search_terms: dict = rule_data.get("search_terms", {})
    scope = rule_data.get("scope", {})
    scope = scope if isinstance(scope, dict) else {}
    domain_tags: list[str] = rule_data.get("domain_tags") or scope.get("domain_tags", [])
    tool_tags: list[str] = rule_data.get("tool_tags") or scope.get("tool_tags", [])
    action_text: str = rule_data.get("action_instruction", "")

    # Exclude the rule itself if it has an id.
    exclude_id: str | None = rule_data.get("id")

    if project_id is None:
        scope_where = "project_scope = 'global'"
        scope_params: tuple = ()
    else:
        scope_where = "(project_scope = 'global' OR project_id = ?)"
        scope_params = (project_id,)

    # Fetch all non-archived rules for scoring.
    rows = db.fetchall(
        f"SELECT {RULE_COLUMNS} FROM rules "
        f"WHERE status != 'archived' AND {scope_where}",
        scope_params,
    )

    for row in rows:
        row_id = row["id"]
        if row_id == exclude_id:
            continue

        score = 0.0

        # BM25-style trigger token overlap.
        row_trigger_tokens = _tokenize(row["trigger_canonical"] or "")
        overlap = len(trigger_tokens & row_trigger_tokens)
        if trigger_tokens:
            score += overlap / len(trigger_tokens) * 3.0

        # Variant phrase overlap.
        row_variants = loads_json(row["trigger_variants"], [])
        row_variant_texts = _variant_texts(row_variants)
        if variant_texts and row_variant_texts:
            variant_overlap = len(variant_texts & row_variant_texts)
            score += variant_overlap * 2.0

        # Search terms overlap.
        row_search_terms = loads_json(row["search_terms"], {})
        if search_terms and row_search_terms:
            st_keys = set(search_terms.keys()) if isinstance(search_terms, dict) else set()
            row_st_keys = set(row_search_terms.keys()) if isinstance(row_search_terms, dict) else set()
            st_overlap = len(st_keys & row_st_keys)
            score += st_overlap * 1.5

        # Same domain/tag.
        row_domain_tags = loads_json(row["domain_tags"], [])
        row_tool_tags = loads_json(row["tool_tags"], [])
        if domain_tags:
            domain_overlap = len(set(domain_tags) & set(row_domain_tags))
            score += domain_overlap * 1.0
        if tool_tags:
            tool_overlap = len(set(tool_tags) & set(row_tool_tags))
            score += tool_overlap * 1.0

        # Action intent keyword overlap.
        action_tokens = _tokenize(action_text)
        row_action_tokens = _tokenize(row["action_instruction"] or "")
        if action_tokens:
            action_overlap = len(action_tokens & row_action_tokens)
            score += action_overlap / max(len(action_tokens), 1) * 1.5

        if score > 0:
            candidates[row_id] = (_row_to_dict(row), score)

    # Embedding-based recall: augment BM25/token candidates with cosine similarity.
    try:
        from ..search.embedding import (
            EmbeddingClient,
            _cosine,
            _deserialize,
        )
        from ..config import Config

        cfg = Config.load()
        if cfg.embed_enabled and cfg.embed_base_url and cfg.embed_model:
            client = EmbeddingClient(cfg)
            query_text = f"{trigger} {action_text}"
            qvecs = client.embed(query_text, timeout=5)
            if qvecs:
                qvec = qvecs[0]
                # Fetch embeddings for active/trusted rules
                active_rows = db.fetchall(
                    "SELECT rule_id, chunk_index, embedding FROM rule_embeddings "
                    "WHERE model_version = ?",
                    (cfg.embed_model,),
                )
                by_rule: dict[str, list[list[float]]] = {}
                for erow in active_rows:
                    try:
                        vec = _deserialize(erow["embedding"])
                    except ValueError:
                        continue
                    by_rule.setdefault(erow["rule_id"], []).append(vec)

                for rule_id, embeddings in by_rule.items():
                    if rule_id == exclude_id:
                        continue
                    best_cos = max(_cosine(qvec, emb) for emb in embeddings)
                    if best_cos > 0.7:
                        # Add or boost existing candidate
                        embedding_bonus = best_cos * 2.5
                        if rule_id in candidates:
                            existing_dict, existing_score = candidates[rule_id]
                            candidates[rule_id] = (existing_dict, existing_score + embedding_bonus)
                        else:
                            # Fetch the rule row for this candidate
                            rule_row = db.fetchone(
                                f"SELECT {RULE_COLUMNS} FROM rules "
                                f"WHERE id = ? AND status != 'archived' AND {scope_where}",
                                (rule_id,) + scope_params,
                            )
                            if rule_row:
                                candidates[rule_id] = (_row_to_dict(rule_row), embedding_bonus)
    except Exception as exc:
        log.debug("embedding recall skipped: %s", exc)

    # Recent fallback: include recently updated rules not yet matched (spec 8.1)
    if len(candidates) < limit:
        recent_rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules WHERE status IN ('active', 'trusted') "
            f"AND {scope_where} ORDER BY updated_at DESC LIMIT ?",
            scope_params + (min(5, limit - len(candidates)),),
        )
        for row in recent_rows:
            row_id = row["id"]
            if row_id not in candidates:
                candidates[row_id] = (_row_to_dict(row), 0.1)

    # Archived fingerprint negative memory (spec 8.1)
    fp_rows = db.fetchall(
        "SELECT id, blocked_trigger_area, blocked_action_area, archive_strength "
        "FROM archived_fingerprints "
        "WHERE archive_strength IN ('user', 'system') LIMIT 20"
    )

    # Sort by score descending, return top limit.
    sorted_candidates = sorted(
        candidates.values(), key=lambda x: x[1], reverse=True
    )
    results = []
    for entry in sorted_candidates[:limit]:
        # Attach any fingerprints whose blocked area overlaps this candidate's trigger/action
        candidate_trigger = (entry[0].get("trigger_canonical") or "").lower()
        candidate_action = (entry[0].get("action_instruction") or "").lower()
        related_fps = []
        for fp in fp_rows:
            fp_trigger = (fp["blocked_trigger_area"] or "").lower()
            fp_action = (fp["blocked_action_area"] or "").lower()
            if (fp_trigger and fp_trigger in candidate_trigger) or \
               (fp_action and fp_action in candidate_action) or \
               (candidate_trigger and candidate_trigger in fp_trigger):
                related_fps.append({
                    "id": fp["id"],
                    "strength": fp["archive_strength"],
                    "blocked_trigger": fp["blocked_trigger_area"],
                })
        candidate_dict = {**entry[0], "archived_fingerprints": related_fps}
        results.append(candidate_dict)
    return results


# ---------------------------------------------------------------------------
# validate_merge_transaction
# ---------------------------------------------------------------------------


def validate_merge_transaction(
    existing_rule: dict | None,
    proposed_new: dict,
    merge_decision: MergeDecision,
    synthetic_passed: bool,
    fingerprint_clear: bool,
    matcher_compiled: bool,
    final_admission_passed: bool = True,
) -> bool:
    """Validate that destructive merge operations meet all preconditions.

    Merge operations that weaken/suppress/archive/replace existing ONLY apply
    if the proposed rule passes ALL 4 checks (section 8.4 final paragraph):
    1. Compilation success (matcher_compiled)
    2. Archived-fingerprint checks (fingerprint_clear)
    3. Synthetic retrieval evaluation (synthetic_passed)
    4. Final admission policy (final_admission_passed)

    For update_existing_fields: treated as destructive when the operation
    changes trigger/variants/concepts/excluded_contexts. Check via merge_decision
    lineage_record metadata if available; otherwise treat all
    update_existing_fields as requiring validation.

    Returns True if the transaction is valid and may proceed.
    """
    destructive_ops: frozenset[str] = frozenset((
        "replace_existing",
        "suppress_existing",
        "archive_existing",
        "update_existing_fields",
    ))

    # Non-destructive operations always pass validation.
    if merge_decision.operation not in destructive_ops:
        return True

    # All four gates must pass for destructive operations.
    if not synthetic_passed:
        return False

    if not fingerprint_clear:
        return False

    if not matcher_compiled:
        return False

    if not final_admission_passed:
        return False

    return True


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _keep_both(target_id: str | None, reason: str) -> MergeDecision:
    return MergeDecision(
        operation="keep_both",
        target_rule_id=target_id,
        reason=reason,
        requires_synthetic_reeval=False,
        lineage_record=None,
    )


def _lineage(target_id: str | None, operation: str, reason: str) -> dict:
    return {
        "old_rule_id": target_id,
        "operation": operation,
        "reason": reason,
    }


def _new_evidence_improves(existing_rule: dict, new_rule_data: dict) -> bool:
    """Check whether the new rule has MORE variants, concepts, excluded_contexts, or near_miss_examples.

    Returns True only if the new rule provides strictly more evidence in at
    least one of these dimensions without reducing any other.
    """
    def _len(obj, key: str) -> int:
        val = obj.get(key)
        if isinstance(val, list):
            return len(val)
        if isinstance(val, str) and val:
            from nokori.db import loads_json
            parsed = loads_json(val, [])
            return len(parsed) if isinstance(parsed, list) else 0
        return 0

    fields = ("variant_count", "concepts", "excluded_contexts", "near_miss_examples")

    has_improvement = False
    for field in fields:
        if field == "variant_count":
            existing_count = _len(existing_rule, "trigger_variants") or _len(
                existing_rule, "variants"
            )
            new_count = _len(new_rule_data, "variants") or _len(
                new_rule_data, "trigger_variants"
            )
        else:
            existing_count = _len(existing_rule, field)
            new_count = _len(new_rule_data, field)
        if new_count < existing_count:
            return False
        if new_count > existing_count:
            has_improvement = True

    return has_improvement


def _action_semantics_unchanged(existing_rule: dict, new_rule_data: dict) -> bool:
    """Check whether action instruction semantics are unchanged."""
    existing_action = (existing_rule.get("action_instruction") or "").strip()
    new_action = (new_rule_data.get("action_instruction") or "").strip()
    # If the new rule does not provide an action, semantics are unchanged.
    if not new_action:
        return True
    # Exact match is the conservative check.
    return existing_action == new_action


def _existing_has_weak_history(existing_rule: dict) -> bool:
    """Check whether existing rule has weak posthoc history or repeated FPs."""
    observed = float(existing_rule.get("observed_usefulness_score", 0.0))
    fp_score = float(existing_rule.get("false_positive_score", 0.0))
    return observed < 0.3 or fp_score >= 0.4


def _is_system_created_harmful_or_obsolete(existing_rule: dict) -> bool:
    """Check whether existing rule is system-created and harmful/obsolete."""
    origin = existing_rule.get("source_origin", "")
    if origin not in ("transcript_extraction", "external_source_material"):
        return False
    # User-created rules (those with activation_origin implying user action)
    # are not subject to automatic archive.
    activation = existing_rule.get("activation_origin") or ""
    if "user" in activation.lower():
        return False
    harmful = float(existing_rule.get("harmful_score", 0.0))
    quality = float(existing_rule.get("quality_score", 0.0))
    return harmful >= 0.5 or quality < 0.3


def _user_archived_opposite(existing_rule: dict, new_rule_data: dict) -> bool:
    """Check if user archived a rule in the opposite direction.

    If existing has archive_strength=user for related content, the system
    should not auto-archive it in the other direction.
    """
    # This checks a flag that the merge pipeline should set based on
    # archived_fingerprints lookup.
    return bool(new_rule_data.get("user_archived_opposite", False))


def _existing_usefulness_weak_or_stale(existing_rule: dict) -> bool:
    """Check whether existing trusted rule's usefulness is weak or stale."""
    observed = float(existing_rule.get("observed_usefulness_score", 0.0))
    first_useful = existing_rule.get("first_observed_useful_at")
    if not first_useful:
        return True
    try:
        from datetime import datetime, timedelta

        ts = str(first_useful).replace("Z", "+00:00")
        first_dt = datetime.fromisoformat(ts)
        if first_dt.tzinfo is None:
            first_dt = first_dt.astimezone()
        from nokori.utils.time import local_now
        if local_now() - first_dt > timedelta(days=90):
            return True
    except (TypeError, ValueError):
        return True
    return observed < 0.4


def _tokenize(text: str) -> set[str]:
    """Simple whitespace tokenizer for BM25-style scoring."""
    if not text:
        return set()
    # Lowercase and split on non-alphanumeric boundaries.
    import re

    tokens = re.findall(r"[a-z0-9一-鿿]+", text.lower())
    # Filter very short tokens.
    return {t for t in tokens if len(t) > 1}


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return {key: row[key] for key in row.keys()}
