from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import Rule

from .connection import Db


def loads_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return _json_default_copy(default)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return _json_default_copy(default)


def _json_default_copy(default: Any) -> Any:
    if isinstance(default, list):
        return list(default)
    if isinstance(default, dict):
        return dict(default)
    return default


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def row_to_rule(row: sqlite3.Row) -> Rule:
    from ..models import Rule

    return Rule(
        id=row["id"],
        short_id=row["short_id"],
        schema_version=row["schema_version"],
        rule_version=row["rule_version"],
        created_by_pipeline_version=row["created_by_pipeline_version"],
        runtime_policy_version=row["runtime_policy_version"],
        last_rewritten_by_role=row["last_rewritten_by_role"],
        status=row["status"],
        severity=row["severity"],
        trigger_canonical=row["trigger_canonical"],
        trigger_canonical_zh=row["trigger_canonical_zh"],
        concepts=loads_json(row["concepts"], []),
        concept_aliases=loads_json(row["concept_aliases"], []),
        required_concept_groups=loads_json(row["required_concept_groups"], []),
        excluded_contexts=loads_json(row["excluded_contexts"], []),
        non_generalization_boundaries=loads_json(row["non_generalization_boundaries"], []),
        near_miss_examples=loads_json(row["near_miss_examples"], []),
        trigger_variants=loads_json(row["trigger_variants"], []),
        trigger_variants_zh=loads_json(row["trigger_variants_zh"], []),
        search_terms=loads_json(row["search_terms"], {}),
        action_instruction=row["action_instruction"],
        action_instruction_zh=row["action_instruction_zh"],
        allowed_behavior=loads_json(row["allowed_behavior"], []),
        forbidden_behavior=loads_json(row["forbidden_behavior"], []),
        domain_tags=loads_json(row["domain_tags"], []),
        tool_tags=loads_json(row["tool_tags"], []),
        path_patterns=loads_json(row["path_patterns"], []),
        language_hints=loads_json(row["language_hints"], []),
        transcript_ref=row["transcript_ref"],
        evidence_quotes=loads_json(row["evidence_quotes"], []),
        quality_score=row["quality_score"],
        evidence_support_score=row["evidence_support_score"],
        specificity_score=row["specificity_score"],
        retrieval_readiness_score=row["retrieval_readiness_score"],
        observed_usefulness_score=row["observed_usefulness_score"],
        plausible_usefulness_score=row["plausible_usefulness_score"],
        false_positive_score=row["false_positive_score"],
        harmful_score=row["harmful_score"],
        source_origin=row["source_origin"],
        activation_origin=row["activation_origin"],
        first_observed_useful_at=row["first_observed_useful_at"],
        trusted_at=row["trusted_at"],
        suppressed_at=row["suppressed_at"],
        project_scope=row["project_scope"],
        project_id=row["project_id"],
        archived_reason=row["archived_reason"],
        replacement_id=row["replacement_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


RULE_COLUMNS = (
    "id, short_id, schema_version, rule_version, "
    "created_by_pipeline_version, runtime_policy_version, last_rewritten_by_role, "
    "status, severity, "
    "trigger_canonical, trigger_canonical_zh, "
    "concepts, concept_aliases, required_concept_groups, excluded_contexts, "
    "non_generalization_boundaries, "
    "near_miss_examples, trigger_variants, trigger_variants_zh, search_terms, "
    "action_instruction, action_instruction_zh, "
    "allowed_behavior, forbidden_behavior, "
    "domain_tags, tool_tags, path_patterns, language_hints, transcript_ref, evidence_quotes, "
    "quality_score, evidence_support_score, specificity_score, retrieval_readiness_score, "
    "observed_usefulness_score, plausible_usefulness_score, false_positive_score, harmful_score, "
    "source_origin, activation_origin, first_observed_useful_at, "
    "trusted_at, suppressed_at, "
    "project_scope, project_id, "
    "archived_reason, replacement_id, "
    "created_at, updated_at"
)

# Hot-path retrieval: skip archival/review JSON columns that BM25/evidence never read.
SEARCH_RULE_COLUMNS = (
    "id, short_id, schema_version, rule_version, "
    "created_by_pipeline_version, runtime_policy_version, last_rewritten_by_role, "
    "status, severity, "
    "trigger_canonical, trigger_canonical_zh, "
    "concepts, required_concept_groups, excluded_contexts, "
    "trigger_variants, trigger_variants_zh, search_terms, "
    "action_instruction, action_instruction_zh, "
    "domain_tags, "
    "observed_usefulness_score, false_positive_score, "
    "source_origin, first_observed_useful_at, "
    "project_scope, project_id, "
    "created_at, updated_at"
)


def row_to_search_rule(row: sqlite3.Row) -> Rule:
    """Build a Rule for retrieval with defaults for columns not selected."""
    from ..models import Rule

    return Rule(
        id=row["id"],
        short_id=row["short_id"],
        schema_version=row["schema_version"],
        rule_version=row["rule_version"],
        created_by_pipeline_version=row["created_by_pipeline_version"],
        runtime_policy_version=row["runtime_policy_version"],
        last_rewritten_by_role=row["last_rewritten_by_role"],
        status=row["status"],
        severity=row["severity"],
        trigger_canonical=row["trigger_canonical"],
        trigger_canonical_zh=row["trigger_canonical_zh"],
        concepts=loads_json(row["concepts"], []),
        required_concept_groups=loads_json(row["required_concept_groups"], []),
        excluded_contexts=loads_json(row["excluded_contexts"], []),
        trigger_variants=loads_json(row["trigger_variants"], []),
        trigger_variants_zh=loads_json(row["trigger_variants_zh"], []),
        search_terms=loads_json(row["search_terms"], {}),
        action_instruction=row["action_instruction"],
        action_instruction_zh=row["action_instruction_zh"],
        domain_tags=loads_json(row["domain_tags"], []),
        observed_usefulness_score=row["observed_usefulness_score"],
        false_positive_score=row["false_positive_score"],
        source_origin=row["source_origin"],
        first_observed_useful_at=row["first_observed_useful_at"],
        project_scope=row["project_scope"],
        project_id=row["project_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def total_rule_count(db: Db) -> int:
    """Rules in injection pool (active + trusted)."""
    row = db.fetchone("SELECT COUNT(*) AS n FROM rules WHERE status IN ('active', 'trusted')")
    return int(row["n"]) if row else 0


def retrieval_pool_count(db: Db) -> int:
    """Rules visible to retrieval (formal + shadow pool, i.e. all non-archived)."""
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rules WHERE status != 'archived'"
    )
    return int(row["n"]) if row else 0


def fetch_rules(
    db: Db,
    *,
    statuses: tuple[str, ...] | None = None,
    project_id: str | None = None,
    global_only: bool = False,
    project_scope_exact: bool = False,
    source_origins: tuple[str, ...] | None = None,
    severities: tuple[str, ...] | None = None,
    for_retrieval: bool = False,
) -> list[Rule]:
    where = []
    params: list = []
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        where.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if source_origins:
        placeholders = ",".join("?" * len(source_origins))
        where.append(f"source_origin IN ({placeholders})")
        params.extend(source_origins)
    if severities:
        placeholders = ",".join("?" * len(severities))
        where.append(f"severity IN ({placeholders})")
        params.extend(severities)
    if global_only:
        where.append("project_scope = 'global'")
    elif project_id is not None:
        if project_scope_exact:
            where.append("(project_id = ? AND project_scope != 'global')")
        else:
            where.append("(project_scope = 'global' OR project_id = ?)")
        params.append(project_id)
    columns = SEARCH_RULE_COLUMNS if for_retrieval else RULE_COLUMNS
    mapper = row_to_search_rule if for_retrieval else row_to_rule
    sql = f"SELECT {columns} FROM rules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC"
    return [mapper(r) for r in db.fetchall(sql, tuple(params))]


def fetch_rule_by_short_id(db: Db, short_id: str) -> Rule | None:
    row = db.fetchone(f"SELECT {RULE_COLUMNS} FROM rules WHERE short_id = ?", (short_id,))
    return row_to_rule(row) if row else None


def fetch_rules_by_short_ids(db: Db, short_ids: list[str]) -> dict[str, Rule]:
    """Batch-fetch rules keyed by short_id. Avoids N+1 queries on import."""
    if not short_ids:
        return {}
    result: dict[str, Rule] = {}
    # SQLite limits IN (...) placeholders; chunk to stay well under the bound.
    for start in range(0, len(short_ids), 500):
        chunk = short_ids[start : start + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = db.fetchall(
            f"SELECT {RULE_COLUMNS} FROM rules WHERE short_id IN ({placeholders})",
            tuple(chunk),
        )
        for row in rows:
            rule = row_to_rule(row)
            result[rule.short_id] = rule
    return result


def fetch_short_ids(db: Db) -> set[str]:
    rows = db.fetchall("SELECT short_id FROM rules")
    return {r["short_id"] for r in rows}


def fetch_rule_ids(db: Db, *, statuses: tuple[str, ...]) -> list[str]:
    """Fetch only rule IDs matching the given statuses (lightweight)."""
    if not statuses:
        return []
    placeholders = ",".join("?" * len(statuses))
    rows = db.fetchall(
        f"SELECT id FROM rules WHERE status IN ({placeholders})",
        statuses,
    )
    return [r["id"] for r in rows]


def find_rule_id_by_injection(
    db: Db, short_id: str, since_iso: str, *, session_id: str | None = None
) -> str | None:
    """Find rule by short_id fired since cutoff, optionally scoped to session."""
    if session_id is not None:
        row = db.fetchone(
            "SELECT r.id AS id FROM rule_fire_events e JOIN rules r ON r.id = e.rule_id "
            "WHERE e.session_id = ? AND r.short_id = ? AND e.created_at >= ? "
            "ORDER BY e.created_at DESC LIMIT 1",
            (session_id, short_id, since_iso),
        )
    else:
        row = db.fetchone(
            "SELECT r.id AS id FROM rule_fire_events e JOIN rules r ON r.id = e.rule_id "
            "WHERE r.short_id = ? AND e.created_at >= ? "
            "ORDER BY e.created_at DESC LIMIT 1",
            (short_id, since_iso),
        )
    return row["id"] if row else None


def find_rule_id_by_recent_injection(
    db: Db, session_id: str, short_id: str, since_iso: str
) -> str | None:
    return find_rule_id_by_injection(db, short_id, since_iso, session_id=session_id)


def find_rule_id_injected_since(db: Db, short_id: str, since_iso: str) -> str | None:
    return find_rule_id_by_injection(db, short_id, since_iso)


def archive_rule(db: Db, rule_id: str, reason: str, now: str, *, strength: str = "user") -> None:
    # Read rule data before archiving for fingerprint creation
    rule_row = db.fetchone(
        "SELECT trigger_canonical, action_instruction, domain_tags FROM rules WHERE id = ?",
        (rule_id,),
    )

    # Pre-compute fingerprint data (pure, no DB) so any failure here
    # is caught before we open the transaction.
    fp_data = None
    strength_rank = None
    if rule_row:
        try:
            from ..archive.fingerprints import STRENGTH_RANK, compute_fingerprint_data

            strength_rank = STRENGTH_RANK
            domain_tags = loads_json(rule_row["domain_tags"], []) if rule_row["domain_tags"] else []
            fp_data = compute_fingerprint_data(
                rule_id=rule_id,
                trigger_canonical=rule_row["trigger_canonical"] or "",
                action_instruction=rule_row["action_instruction"] or "",
                domain_tags=domain_tags,
                strength=strength,
                created_at=now,
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "fingerprint computation failed for rule=%s: %s",
                rule_id,
                exc,
            )

    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET status = 'archived', archived_reason = ?, "
            "updated_at = ? WHERE id = ?",
            (reason, now, rule_id),
        )
        # Cancel in-flight shadow promotion/recovery (spec section 11:
        # removes from injection, Gate, shadow promotion, and recovery)
        tx.execute(
            "UPDATE rule_shadow_events SET shadow_label = 'unclear' "
            "WHERE rule_id = ? AND shadow_label IS NULL",
            (rule_id,),
        )
        # Create archived fingerprint in the same transaction (atomic with archival)
        if fp_data is not None:
            tx.execute(
                "INSERT INTO archived_fingerprints "
                "(id, signature, scope_summary, blocked_trigger_area, blocked_action_area, "
                "archive_strength, can_be_overridden_by_changed_scope, rule_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(signature) DO NOTHING",
                (
                    fp_data["id"],
                    fp_data["signature"],
                    fp_data["scope_summary"],
                    fp_data["blocked_trigger_area"],
                    fp_data["blocked_action_area"],
                    fp_data["archive_strength"],
                    fp_data["can_be_overridden_by_changed_scope"],
                    fp_data["rule_id"],
                    fp_data["created_at"],
                ),
            )
            # id is uuid4 (no PK conflict); changes()==0 means signature UNIQUE conflict.
            # rule_id stays as first creator — fingerprint is evidence the content was archived.
            if tx.execute("SELECT changes()").fetchone()[0] == 0:
                existing = tx.execute(
                    "SELECT id, archive_strength FROM archived_fingerprints WHERE signature = ?",
                    (fp_data["signature"],),
                ).fetchone()
                if existing and strength_rank:
                    existing_strength = existing["archive_strength"]
                    if strength_rank.get(fp_data["archive_strength"], -1) > strength_rank.get(
                        existing_strength, -1
                    ):
                        # created_at = time of strongest archival event (not first creation)
                        tx.execute(
                            "UPDATE archived_fingerprints SET archive_strength = ?, "
                            "can_be_overridden_by_changed_scope = ?, created_at = ? "
                            "WHERE id = ?",
                            (
                                fp_data["archive_strength"],
                                fp_data["can_be_overridden_by_changed_scope"],
                                fp_data["created_at"],
                                existing["id"],
                            ),
                        )


def _delete_rule_cascade_tx(tx: sqlite3.Connection, rule_id: str) -> None:
    """Remove rule and dependent rows within an existing transaction cursor."""
    # Delete children of fire_events first (they reference fire_event_id)
    tx.execute(
        "DELETE FROM posthoc_jobs WHERE fire_event_id IN "
        "(SELECT id FROM rule_fire_events WHERE rule_id = ?)",
        (rule_id,),
    )
    # Then fire/shadow events themselves
    tx.execute("DELETE FROM rule_fire_events WHERE rule_id = ?", (rule_id,))
    tx.execute("DELETE FROM rule_shadow_events WHERE rule_id = ?", (rule_id,))
    # Other direct dependents
    tx.execute("DELETE FROM rule_reviews WHERE rule_id = ?", (rule_id,))
    tx.execute("DELETE FROM rule_synthetic_evals WHERE rule_id = ?", (rule_id,))
    tx.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
    tx.execute(
        "DELETE FROM rule_lineage WHERE old_rule_id = ? OR new_rule_id = ?", (rule_id, rule_id)
    )
    tx.execute("DELETE FROM archived_fingerprints WHERE rule_id = ?", (rule_id,))
    # Finally the rule itself
    tx.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
