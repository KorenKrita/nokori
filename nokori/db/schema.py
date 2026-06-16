from __future__ import annotations

import sqlite3

from ..errors import DbError

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
CREATE INDEX IF NOT EXISTS idx_rule_embeddings_model ON rule_embeddings(model_version, rule_id);

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


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, col_type: str
) -> None:
    """Add a column if it doesn't already exist (idempotent ALTER)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if not existing:
        return
    if column not in existing:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _create_index_safe(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute CREATE INDEX IF NOT EXISTS, ignoring missing tables."""
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
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
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_rule_embeddings_model ON rule_embeddings(model_version, rule_id)")
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
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_rule_embeddings_model ON rule_embeddings(model_version, rule_id)")
        return
    elif current == 8:
        _add_column_if_missing(conn, "rule_fire_events", "project_id", "TEXT")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_fire_events_rule_project ON rule_fire_events(rule_id, posthoc_label, project_id)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_rule_embeddings_model ON rule_embeddings(model_version, rule_id)")
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        return
    elif current == 9:
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_synthetic_evals_rule ON rule_synthetic_evals(rule_id, rule_version, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_reviews_rule ON rule_reviews(rule_id, decision, created_at)")
        _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS idx_rule_embeddings_model ON rule_embeddings(model_version, rule_id)")
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
