from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import DbError

SCHEMA_VERSION = 1

_DDL_V1 = """
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
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium')),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','active','merged','archived','dormant')),
    evidence_score INTEGER NOT NULL DEFAULT 0,
    evidence_log TEXT NOT NULL DEFAULT '[]',
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit TEXT,
    cross_project_hits INTEGER NOT NULL DEFAULT 0,
    promotion_evidence TEXT NOT NULL DEFAULT '[]',
    project_scope TEXT NOT NULL DEFAULT 'project' CHECK(project_scope IN ('project','global')),
    project_id TEXT,
    superseded_by TEXT,
    archived_reason TEXT,
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

CREATE TABLE IF NOT EXISTS extract_state (
    transcript_path TEXT PRIMARY KEY,
    transcript_mtime REAL NOT NULL,
    extracted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done'
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


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_V1)


_MIGRATIONS = {1: _migrate_to_v1}


class Db:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def schema_version(self) -> int:
        return _read_version(self.conn)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

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
            _migrate(conn)
            return Db(conn)
        except sqlite3.OperationalError as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    raise DbError(f"failed to open db at {path}: {last_err}")


def _migrate(conn: sqlite3.Connection) -> None:
    current = _read_version(conn)
    if current >= SCHEMA_VERSION:
        return
    for v in range(current + 1, SCHEMA_VERSION + 1):
        migrator = _MIGRATIONS.get(v)
        if migrator is None:
            raise DbError(f"missing migration to v{v}")
        try:
            conn.execute("BEGIN")
            migrator(conn)
            _write_version(conn, v)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def loads_json(value: str | None, default):
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
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
        cross_project_hits=row["cross_project_hits"],
        promotion_evidence=loads_json(row["promotion_evidence"], []),
        project_scope=row["project_scope"],
        project_id=row["project_id"],
        superseded_by=row["superseded_by"],
        archived_reason=row["archived_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


RULE_COLUMNS = (
    "id, short_id, trigger_text, trigger_variants, search_terms, behavior, action, "
    "rationale, source_type, confidence, status, evidence_score, evidence_log, "
    "hit_count, last_hit, cross_project_hits, promotion_evidence, project_scope, "
    "project_id, superseded_by, archived_reason, "
    "created_at, updated_at"
)


def fetch_rules(db: "Db", *, statuses: tuple[str, ...] | None = None,
                project_id: str | None = None) -> list:
    where = []
    params: list = []
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        where.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if project_id is not None:
        where.append("(project_scope = 'global' OR project_id = ? OR project_id IS NULL)")
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


def log_injection(
    db: "Db",
    rule_id: str,
    session_id: str,
    prompt_hash: str,
    level: str,
    now: str,
) -> None:
    with db.transaction() as tx:
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


def find_rule_id_by_recent_injection(
    db: "Db", session_id: str, short_id: str, since_iso: str
) -> str | None:
    row = db.fetchone(
        "SELECT r.id AS id FROM injections i JOIN rules r ON r.id = i.rule_id "
        "WHERE i.session_id = ? AND r.short_id = ? AND i.created_at >= ? "
        "ORDER BY i.created_at DESC LIMIT 1",
        (session_id, short_id, since_iso),
    )
    return row["id"] if row else None


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
