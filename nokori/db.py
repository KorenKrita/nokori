from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Rule

from .errors import DbError

SCHEMA_VERSION = 10

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    short_id TEXT UNIQUE NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 6,
    rule_version INTEGER NOT NULL DEFAULT 1,
    created_by_pipeline_version TEXT,
    runtime_policy_version TEXT DEFAULT '1.0.0',
    last_rewritten_by_role TEXT,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','active','trusted','suppressed','archived')),
    severity TEXT NOT NULL DEFAULT 'reminder' CHECK(severity IN ('reminder','high_risk','gate_eligible')),
    trigger_canonical TEXT NOT NULL,
    trigger_canonical_zh TEXT,
    concepts TEXT NOT NULL DEFAULT '[]',
    concept_aliases TEXT NOT NULL DEFAULT '[]',
    required_concept_groups TEXT NOT NULL DEFAULT '[]',
    excluded_contexts TEXT NOT NULL DEFAULT '[]',
    evidence_quotes TEXT NOT NULL DEFAULT '[]',
    non_generalization_boundaries TEXT NOT NULL DEFAULT '[]',
    near_miss_examples TEXT NOT NULL DEFAULT '[]',
    trigger_variants TEXT NOT NULL DEFAULT '[]',
    trigger_variants_zh TEXT NOT NULL DEFAULT '[]',
    search_terms TEXT NOT NULL DEFAULT '{}',
    action_instruction TEXT NOT NULL,
    action_instruction_zh TEXT,
    allowed_behavior TEXT NOT NULL DEFAULT '[]',
    forbidden_behavior TEXT NOT NULL DEFAULT '[]',
    domain_tags TEXT NOT NULL DEFAULT '[]',
    tool_tags TEXT NOT NULL DEFAULT '[]',
    path_patterns TEXT NOT NULL DEFAULT '[]',
    language_hints TEXT NOT NULL DEFAULT '[]',
    transcript_ref TEXT,
    quality_score REAL NOT NULL DEFAULT 0.0,
    evidence_support_score REAL NOT NULL DEFAULT 0.0,
    specificity_score REAL NOT NULL DEFAULT 0.0,
    retrieval_readiness_score REAL NOT NULL DEFAULT 0.0,
    observed_usefulness_score REAL NOT NULL DEFAULT 0.0,
    plausible_usefulness_score REAL NOT NULL DEFAULT 0.0,
    false_positive_score REAL NOT NULL DEFAULT 0.0,
    harmful_score REAL NOT NULL DEFAULT 0.0,
    synthetic_eval_skipped INTEGER NOT NULL DEFAULT 0,
    source_origin TEXT NOT NULL DEFAULT 'transcript_extraction' CHECK(source_origin IN ('transcript_extraction','external_source_material')),
    activation_origin TEXT CHECK(activation_origin IS NULL OR activation_origin IN ('cold_fast_lane','shadow_promotion','merge_replacement','external_shadow_promotion')),
    first_observed_useful_at TEXT,
    trusted_at TEXT,
    suppressed_at TEXT,
    project_scope TEXT NOT NULL DEFAULT 'global' CHECK(project_scope IN ('project','global')),
    project_id TEXT,
    archived_reason TEXT,
    replacement_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_embeddings (
    rule_id TEXT NOT NULL REFERENCES rules(id),
    chunk_index INTEGER NOT NULL DEFAULT 0,
    embedding BLOB NOT NULL,
    model_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (rule_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS rule_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT,
    model_id TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    output_json TEXT,
    scores TEXT,
    decision TEXT,
    rule_id TEXT REFERENCES rules(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_synthetic_evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT REFERENCES rules(id),
    rule_version INTEGER,
    runtime_policy_version TEXT,
    tokenizer_version TEXT,
    matcher_compiler_version TEXT,
    concept_compiler_version TEXT,
    embedding_profile_version TEXT,
    trigger_idf_pool_version TEXT,
    benchmark_version TEXT,
    eval_cases TEXT,
    eval_results TEXT,
    expected_decisions TEXT,
    passed INTEGER,
    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS trigger_idf_stats (
    pool_version TEXT PRIMARY KEY,
    rule_pool_size INTEGER,
    eligible_rule_set_hash TEXT,
    tokenizer_version TEXT,
    matcher_compiler_version TEXT,
    generic_token_policy_version TEXT,
    concept_compiler_version TEXT,
    df_by_token TEXT,
    dynamic_threshold REAL,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_fire_events (
    id TEXT PRIMARY KEY,
    rule_id TEXT REFERENCES rules(id),
    session_id TEXT,
    injected_rule_version INTEGER,
    injected_trigger_snapshot TEXT,
    injected_action_snapshot TEXT,
    injected_structured_snapshot TEXT,
    trigger_idf_pool_version TEXT,
    runtime_policy_version TEXT,
    embedding_profile_version TEXT,
    prompt_hash TEXT,
    transcript_window_ref TEXT,
    turn_index INTEGER,
    level TEXT CHECK(level IN ('hot','warm','gate')),
    decision_reason TEXT,
    decision_features TEXT,
    bounded_window_ref TEXT,
    posthoc_label TEXT CHECK(posthoc_label IS NULL OR posthoc_label IN ('observed_useful','plausible_useful','irrelevant','harmful','unclear')),
    posthoc_reason_code TEXT CHECK(posthoc_reason_code IS NULL OR posthoc_reason_code IN ('useful_prevented_error','useful_improved_quality','useful_followed_preference','irrelevant_not_applicable','irrelevant_redundant','irrelevant_unused','harmful_distracted','harmful_wrong_scope','harmful_blocked_valid_action')),
    posthoc_score REAL,
    project_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_shadow_events (
    id TEXT PRIMARY KEY,
    rule_id TEXT REFERENCES rules(id),
    session_id TEXT,
    shadow_rule_version INTEGER,
    shadow_trigger_snapshot TEXT,
    shadow_action_snapshot TEXT,
    shadow_structured_snapshot TEXT,
    status_at_match TEXT CHECK(status_at_match IN ('candidate','suppressed')),
    shadow_type TEXT CHECK(shadow_type IN ('candidate_probe','suppression_recovery')),
    prompt_hash TEXT,
    transcript_window_ref TEXT,
    bounded_window_ref TEXT,
    matched_level TEXT CHECK(matched_level IN ('cold','warm_candidate','hot_candidate')),
    decision_features TEXT,
    trigger_idf_pool_version TEXT,
    runtime_policy_version TEXT,
    embedding_profile_version TEXT,
    shadow_label TEXT CHECK(shadow_label IS NULL OR shadow_label IN ('would_help_high','would_help_low','irrelevant','risky','near_miss','unclear')),
    evaluator_model_id TEXT,
    context_fingerprint TEXT,
    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS rule_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_rule_id TEXT,
    new_rule_id TEXT,
    operation TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archived_fingerprints (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    scope_summary TEXT,
    blocked_trigger_area TEXT,
    blocked_action_area TEXT,
    archive_strength TEXT CHECK(archive_strength IN ('user','system','replacement')),
    can_be_overridden_by_changed_scope INTEGER NOT NULL DEFAULT 0,
    rule_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_jobs (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    model_id TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    output_json TEXT,
    retries INTEGER DEFAULT 0,
    next_retry_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_ingest_jobs (
    id TEXT PRIMARY KEY,
    transcript_segment_ref TEXT,
    segment_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    ttl_expires_at TEXT,
    extractor_prompt_version TEXT,
    pipeline_checkpoint TEXT,
    retries INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posthoc_jobs (
    id TEXT PRIMARY KEY,
    fire_event_id TEXT REFERENCES rule_fire_events(id),
    window_payload_hash TEXT,
    redacted_window_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    retries INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extract_state (
    transcript_path TEXT PRIMARY KEY,
    transcript_mtime REAL NOT NULL,
    extracted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    last_byte_offset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS maintenance_meta (
    key TEXT PRIMARY KEY,
    last_run TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status);
CREATE INDEX IF NOT EXISTS idx_rules_project ON rules(project_scope, project_id);
CREATE INDEX IF NOT EXISTS idx_fire_events_rule ON rule_fire_events(rule_id, created_at);
CREATE INDEX IF NOT EXISTS idx_fire_events_session ON rule_fire_events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_fire_events_rule_label_created ON rule_fire_events(rule_id, posthoc_label, created_at);
CREATE INDEX IF NOT EXISTS idx_fire_events_created ON rule_fire_events(created_at);
CREATE INDEX IF NOT EXISTS idx_fire_events_session_prompt ON rule_fire_events(session_id, prompt_hash);
CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id);
CREATE INDEX IF NOT EXISTS idx_shadow_events_rule ON rule_shadow_events(rule_id, created_at);
CREATE INDEX IF NOT EXISTS idx_shadow_events_fingerprint ON rule_shadow_events(rule_id, context_fingerprint);
CREATE INDEX IF NOT EXISTS idx_shadow_events_rule_label ON rule_shadow_events(rule_id, shadow_label);
CREATE INDEX IF NOT EXISTS idx_archived_fp_signature ON archived_fingerprints(signature);
CREATE INDEX IF NOT EXISTS idx_llm_jobs_status ON llm_jobs(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_posthoc_jobs_status ON posthoc_jobs(status);
CREATE INDEX IF NOT EXISTS idx_posthoc_jobs_fire ON posthoc_jobs(fire_event_id);

CREATE TABLE IF NOT EXISTS hook_events (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    source TEXT NOT NULL,
    outcome TEXT,
    prompt_snippet TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_events (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    source TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'system',
    model_id TEXT,
    error_type TEXT NOT NULL,
    message TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hook_events_session ON hook_events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_hook_events_source ON hook_events(source, created_at);
CREATE INDEX IF NOT EXISTS idx_hook_events_created ON hook_events(created_at);
CREATE INDEX IF NOT EXISTS idx_error_events_role_model ON error_events(role, model_id, error_type);
CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events(created_at);
CREATE INDEX IF NOT EXISTS idx_error_events_source ON error_events(source, created_at);
CREATE INDEX IF NOT EXISTS idx_error_events_session ON error_events(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at);
CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at);
"""


def _read_version(conn: sqlite3.Connection) -> int:
    cur = conn.execute("PRAGMA user_version")
    row = cur.fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _write_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


class Db:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._in_tx = False

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._in_tx:
            raise DbError("nested database transaction")
        self._in_tx = True
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._in_tx = False

    def schema_version(self) -> int:
        return _read_version(self.conn)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        cur = self.conn.execute(sql, params)
        return cur.fetchone()  # type: ignore[no-any-return]

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        return cur.fetchall()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def open_db(path: Path) -> Db:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            conn = _connect(path)
            try:
                _migrate(conn)
            except Exception:
                conn.close()
                raise
            return Db(conn)
        except (sqlite3.OperationalError, DbError) as e:
            if isinstance(e, DbError) and "locked" not in str(e).lower():
                raise
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    remediation: str
    if last_err and "locked" in str(last_err).lower():
        remediation = "Check for orphaned nokori processes: ps aux | grep nokori"
    else:
        remediation = (
            "Ensure ~/.nokori exists with mode 700: mkdir -p ~/.nokori && chmod 700 ~/.nokori"
        )
    raise DbError(
        f"failed to open db at {path}: {last_err}",
        remediation=remediation,
    )


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, col_type: str
) -> None:
    """Add a column if it doesn't already exist (idempotent ALTER)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _migrate(conn: sqlite3.Connection) -> None:
    current = _read_version(conn)
    if current > SCHEMA_VERSION:
        raise DbError("rules.db was created by a newer nokori; upgrade this installation")
    if current >= SCHEMA_VERSION:
        return
    if current == 0:
        script = f"BEGIN;\n{_SCHEMA_DDL}\nPRAGMA user_version = {int(SCHEMA_VERSION)};\nCOMMIT;\n"
    elif current == 6:
        script = (
            "BEGIN;\n"
            "CREATE TABLE IF NOT EXISTS hook_events (\n"
            "    id TEXT PRIMARY KEY,\n"
            "    session_id TEXT,\n"
            "    source TEXT NOT NULL,\n"
            "    outcome TEXT,\n"
            "    prompt_snippet TEXT,\n"
            "    details TEXT,\n"
            "    created_at TEXT NOT NULL\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS error_events (\n"
            "    id TEXT PRIMARY KEY,\n"
            "    session_id TEXT,\n"
            "    source TEXT NOT NULL,\n"
            "    role TEXT NOT NULL DEFAULT 'system',\n"
            "    model_id TEXT,\n"
            "    error_type TEXT NOT NULL,\n"
            "    message TEXT,\n"
            "    details TEXT,\n"
            "    created_at TEXT NOT NULL\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS idx_hook_events_session ON hook_events(session_id, created_at);\n"
            "CREATE INDEX IF NOT EXISTS idx_hook_events_source ON hook_events(source, created_at);\n"
            "CREATE INDEX IF NOT EXISTS idx_hook_events_created ON hook_events(created_at);\n"
            "CREATE INDEX IF NOT EXISTS idx_error_events_role_model ON error_events(role, model_id, error_type);\n"
            "CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events(created_at);\n"
            "CREATE INDEX IF NOT EXISTS idx_error_events_source ON error_events(source, created_at);\n"
            "CREATE INDEX IF NOT EXISTS idx_error_events_session ON error_events(session_id, created_at);\n"
            f"PRAGMA user_version = {int(SCHEMA_VERSION)};\n"
            "COMMIT;\n"
        )
        try:
            conn.executescript(script)
        except Exception as e:
            raise DbError(f"failed to initialize rules.db: {e}") from e
        _add_column_if_missing(conn, "transcript_ingest_jobs", "pipeline_checkpoint", "TEXT")
        _add_column_if_missing(conn, "rule_fire_events", "project_id", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        return
    elif current == 7:
        script = (
            "BEGIN;\n"
            f"PRAGMA user_version = {int(SCHEMA_VERSION)};\n"
            "COMMIT;\n"
        )
        try:
            conn.executescript(script)
        except Exception as e:
            raise DbError(f"failed to migrate rules.db from v7 to v{SCHEMA_VERSION}: {e}") from e
        _add_column_if_missing(conn, "transcript_ingest_jobs", "pipeline_checkpoint", "TEXT")
        _add_column_if_missing(conn, "rule_fire_events", "project_id", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        return
    elif current == 8:
        _add_column_if_missing(conn, "rule_fire_events", "project_id", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        return
    elif current == 9:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        return
    else:
        raise DbError(
            "rules.db schema version is incompatible with this nokori. "
            "Use a fresh NOKORI_DATA_DIR or export rules and reinitialize."
        )
    try:
        conn.executescript(script)
    except Exception as e:
        raise DbError(f"failed to initialize rules.db: {e}") from e


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


def row_to_rule(row: sqlite3.Row) -> "Rule":
    from .models import Rule

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


def total_rule_count(db: "Db") -> int:
    """Rules in injection pool (active + trusted)."""
    row = db.fetchone("SELECT COUNT(*) AS n FROM rules WHERE status IN ('active', 'trusted')")
    return int(row["n"]) if row else 0


def fetch_rules(
    db: "Db",
    *,
    statuses: tuple[str, ...] | None = None,
    project_id: str | None = None,
    global_only: bool = False,
    project_scope_exact: bool = False,
    source_origins: tuple[str, ...] | None = None,
    severities: tuple[str, ...] | None = None,
) -> list:
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
    sql = f"SELECT {RULE_COLUMNS} FROM rules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC"
    return [row_to_rule(r) for r in db.fetchall(sql, tuple(params))]


def fetch_rule_by_short_id(db: "Db", short_id: str) -> "Rule | None":
    row = db.fetchone(f"SELECT {RULE_COLUMNS} FROM rules WHERE short_id = ?", (short_id,))
    return row_to_rule(row) if row else None


def fetch_short_ids(db: "Db") -> set[str]:
    rows = db.fetchall("SELECT short_id FROM rules")
    return {r["short_id"] for r in rows}


def fetch_rule_ids(db: "Db", *, statuses: tuple[str, ...]) -> list[str]:
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
    db: "Db", short_id: str, since_iso: str, *, session_id: str | None = None
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
    db: "Db", session_id: str, short_id: str, since_iso: str
) -> str | None:
    return find_rule_id_by_injection(db, short_id, since_iso, session_id=session_id)


def find_rule_id_injected_since(db: "Db", short_id: str, since_iso: str) -> str | None:
    return find_rule_id_by_injection(db, short_id, since_iso)


def archive_rule(db: "Db", rule_id: str, reason: str, now: str, *, strength: str = "user") -> None:
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
            from .archive.fingerprints import STRENGTH_RANK, compute_fingerprint_data

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
    # Finally the rule itself
    tx.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
