from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import DbError

SCHEMA_VERSION = 5

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    short_id TEXT UNIQUE NOT NULL,
    trigger_text TEXT NOT NULL,
    trigger_variants TEXT NOT NULL DEFAULT '[]',
    search_terms TEXT NOT NULL DEFAULT '{}',
    behavior TEXT,
    action TEXT NOT NULL,
    rationale TEXT,
    source_type TEXT NOT NULL CHECK(source_type IN ('correction','preference','solution','anti_pattern')),
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','active','merged','archived','dormant')),
    evidence_score INTEGER NOT NULL DEFAULT 0,
    evidence_log TEXT NOT NULL DEFAULT '[]',
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit TEXT,
    shadow_hit_count INTEGER NOT NULL DEFAULT 0,
    promotion_evidence TEXT NOT NULL DEFAULT '[]',
    project_scope TEXT NOT NULL DEFAULT 'project' CHECK(project_scope IN ('project','global')),
    project_id TEXT,
    superseded_by TEXT,
    archived_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    trigger_text_zh TEXT,
    behavior_zh TEXT,
    action_zh TEXT,
    rationale_zh TEXT,
    trigger_variants_zh TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS rule_embeddings (
    rule_id TEXT NOT NULL REFERENCES rules(id),
    chunk_index INTEGER NOT NULL DEFAULT 0,
    embedding BLOB NOT NULL,
    model_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (rule_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS extract_state (
    transcript_path TEXT PRIMARY KEY,
    transcript_mtime REAL NOT NULL,
    extracted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    last_byte_offset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS injections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL REFERENCES rules(id),
    session_id TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    level TEXT NOT NULL CHECK(level IN ('hot','warm')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_injections_session ON injections(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_injections_rule ON injections(rule_id);

CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status);
CREATE INDEX IF NOT EXISTS idx_rules_project ON rules(project_scope, project_id);
CREATE INDEX IF NOT EXISTS idx_rules_shadow ON rules(status, confidence, source_type, project_scope);

CREATE TABLE IF NOT EXISTS maintenance_meta (
    key TEXT PRIMARY KEY,
    last_run TEXT NOT NULL
);
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
        return cur.fetchone()

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
    raise DbError(f"failed to open db at {path}: {last_err}")


def _migrate(conn: sqlite3.Connection) -> None:
    current = _read_version(conn)
    if current > SCHEMA_VERSION:
        raise DbError(
            "rules.db was created by a newer nokori; upgrade this installation"
        )
    if current >= SCHEMA_VERSION:
        return
    if current == 0:
        script = (
            "BEGIN;\n"
            f"{_SCHEMA_DDL}\n"
            f"PRAGMA user_version = {int(SCHEMA_VERSION)};\n"
            "COMMIT;\n"
        )
    elif current == 2:
        script = (
            "BEGIN;\n"
            "ALTER TABLE extract_state ADD COLUMN last_byte_offset INTEGER NOT NULL DEFAULT 0;\n"
            "ALTER TABLE rules ADD COLUMN trigger_text_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN behavior_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN action_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN rationale_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN trigger_variants_zh TEXT NOT NULL DEFAULT '[]';\n"
            f"PRAGMA user_version = {int(SCHEMA_VERSION)};\n"
            "COMMIT;\n"
        )
    elif current == 3:
        script = (
            "BEGIN;\n"
            "ALTER TABLE rules ADD COLUMN trigger_text_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN behavior_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN action_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN rationale_zh TEXT;\n"
            "ALTER TABLE rules ADD COLUMN trigger_variants_zh TEXT NOT NULL DEFAULT '[]';\n"
            f"PRAGMA user_version = {int(SCHEMA_VERSION)};\n"
            "COMMIT;\n"
        )
    elif current == 4:
        script = (
            "BEGIN;\n"
            "ALTER TABLE rules ADD COLUMN trigger_variants_zh TEXT NOT NULL DEFAULT '[]';\n"
            f"PRAGMA user_version = {int(SCHEMA_VERSION)};\n"
            "COMMIT;\n"
        )
    else:
        raise DbError(
            "rules.db format is not compatible with this nokori; "
            "use a fresh NOKORI_DATA_DIR or nokori export + reset"
        )
    try:
        conn.executescript(script)
    except Exception as e:
        raise DbError(f"failed to initialize rules.db: {e}") from e


def loads_json(value: str | None, default):
    if value is None or value == "":
        return _json_default_copy(default)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return _json_default_copy(default)


def _json_default_copy(default):
    if isinstance(default, list):
        return list(default)
    if isinstance(default, dict):
        return dict(default)
    return default


def dumps_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def row_to_rule(row):
    from .models import Rule

    return Rule(
        id=row["id"],
        short_id=row["short_id"],
        trigger_text=row["trigger_text"],
        trigger_variants=loads_json(row["trigger_variants"], []),
        search_terms=loads_json(row["search_terms"], {}),
        behavior=row["behavior"],
        action=row["action"],
        rationale=row["rationale"],
        source_type=row["source_type"],
        confidence=row["confidence"],
        status=row["status"],
        evidence_score=row["evidence_score"],
        evidence_log=loads_json(row["evidence_log"], []),
        hit_count=row["hit_count"],
        last_hit=row["last_hit"],
        shadow_hit_count=row["shadow_hit_count"],
        promotion_evidence=loads_json(row["promotion_evidence"], []),
        project_scope=row["project_scope"],
        project_id=row["project_id"],
        superseded_by=row["superseded_by"],
        archived_reason=row["archived_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        trigger_text_zh=row["trigger_text_zh"],
        behavior_zh=row["behavior_zh"],
        action_zh=row["action_zh"],
        rationale_zh=row["rationale_zh"],
        trigger_variants_zh=loads_json(row["trigger_variants_zh"], []),
    )


RULE_COLUMNS = (
    "id, short_id, trigger_text, trigger_variants, search_terms, behavior, action, "
    "rationale, source_type, confidence, status, evidence_score, evidence_log, "
    "hit_count, last_hit, shadow_hit_count, promotion_evidence, project_scope, "
    "project_id, superseded_by, archived_reason, "
    "created_at, updated_at, "
    "trigger_text_zh, behavior_zh, action_zh, rationale_zh, trigger_variants_zh"
)


def total_rule_count(db: "Db") -> int:
    """Rules in searchable pools (active + dormant); used for embedding auto-enable."""
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM rules WHERE status IN ('active', 'dormant')"
    )
    return int(row["n"]) if row else 0


def fetch_rules(
    db: "Db",
    *,
    statuses: tuple[str, ...] | None = None,
    project_id: str | None = None,
    global_only: bool = False,
) -> list:
    where = []
    params: list = []
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        where.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if global_only:
        where.append("project_scope = 'global'")
    elif project_id is not None:
        where.append("(project_scope = 'global' OR project_id = ?)")
        params.append(project_id)
    sql = f"SELECT {RULE_COLUMNS} FROM rules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC"
    return [row_to_rule(r) for r in db.fetchall(sql, tuple(params))]


def fetch_rule_by_short_id(db: "Db", short_id: str):
    row = db.fetchone(
        f"SELECT {RULE_COLUMNS} FROM rules WHERE short_id = ?", (short_id,)
    )
    return row_to_rule(row) if row else None


def fetch_short_ids(db: "Db") -> set[str]:
    rows = db.fetchall("SELECT short_id FROM rules")
    return {r["short_id"] for r in rows}


def log_injections_batch(
    db: "Db",
    session_id: str,
    prompt_hash: str,
    entries: list[tuple[str, str]],
    now: str,
) -> None:
    if not entries:
        return
    with db.transaction() as tx:
        for rule_id, level in entries:
            tx.execute(
                "INSERT INTO injections (rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?)",
                (rule_id, session_id, prompt_hash, level, now),
            )
            if level == "hot":
                tx.execute(
                    "UPDATE rules SET hit_count = hit_count + 1, last_hit = ?, "
                    "updated_at = ? WHERE id = ?",
                    (now, now, rule_id),
                )
            elif level == "warm":
                tx.execute(
                    "UPDATE rules SET last_hit = ?, updated_at = ? WHERE id = ?",
                    (now, now, rule_id),
                )


def find_rule_id_by_injection(
    db: "Db", short_id: str, since_iso: str, *, session_id: str | None = None
) -> str | None:
    """Find rule by short_id injected since cutoff, optionally scoped to session."""
    if session_id is not None:
        row = db.fetchone(
            "SELECT r.id AS id FROM injections i JOIN rules r ON r.id = i.rule_id "
            "WHERE i.session_id = ? AND r.short_id = ? AND i.created_at >= ? "
            "ORDER BY i.created_at DESC LIMIT 1",
            (session_id, short_id, since_iso),
        )
    else:
        row = db.fetchone(
            "SELECT r.id AS id FROM injections i JOIN rules r ON r.id = i.rule_id "
            "WHERE r.short_id = ? AND i.created_at >= ? "
            "ORDER BY i.created_at DESC LIMIT 1",
            (short_id, since_iso),
        )
    return row["id"] if row else None


def find_rule_id_by_recent_injection(
    db: "Db", session_id: str, short_id: str, since_iso: str
) -> str | None:
    return find_rule_id_by_injection(db, short_id, since_iso, session_id=session_id)


def find_rule_id_injected_since(
    db: "Db", short_id: str, since_iso: str
) -> str | None:
    return find_rule_id_by_injection(db, short_id, since_iso)


def fetch_shadow_rules(db: "Db", *, project_id: str | None) -> list:
    """Fetch shadow pool rules: other projects' high-confidence active rules
    with source_type in (correction, anti_pattern, solution)."""
    if project_id is None:
        return []
    rows = db.fetchall(
        f"SELECT {RULE_COLUMNS} FROM rules "
        "WHERE status = 'active' AND confidence = 'high' "
        "AND source_type IN ('correction','anti_pattern','solution') "
        "AND project_scope = 'project' "
        "AND project_id IS NOT NULL AND project_id != ? "
        "ORDER BY updated_at DESC",
        (project_id,),
    )
    return [row_to_rule(r) for r in rows]


def archive_rule(db: "Db", rule_id: str, reason: str, now: str) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE rules SET status = 'archived', archived_reason = ?, "
            "updated_at = ? WHERE id = ?",
            (reason, now, rule_id),
        )


def _delete_rule_cascade_tx(tx, rule_id: str) -> None:
    """Remove rule and dependent rows within an existing transaction cursor."""
    tx.execute("DELETE FROM injections WHERE rule_id = ?", (rule_id,))
    tx.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
    tx.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def delete_rule_cascade(db: "Db", rule_id: str) -> None:
    """Remove rule and dependent rows (foreign_keys=ON)."""
    with db.transaction() as tx:
        _delete_rule_cascade_tx(tx, rule_id)
