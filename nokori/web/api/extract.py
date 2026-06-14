from __future__ import annotations

from fastapi import APIRouter, Query

from nokori.db import loads_json, open_db
from nokori.extract import jobs as job_io
from nokori.utils.sql_batch import batched
from nokori.web.deps import get_config

router = APIRouter()


def _parse_details(raw) -> dict:
    if isinstance(raw, str):
        return loads_json(raw, {})
    return raw if isinstance(raw, dict) else {}


@router.get("/extract/jobs")
def list_extract_jobs():
    cfg = get_config()
    pending = job_io.list_jobs(cfg, status="pending")
    db = open_db(cfg.db_path)
    try:
        done_rows = db.fetchall(
            "SELECT transcript_path, extracted_at FROM extract_state "
            "WHERE status = 'done' ORDER BY extracted_at DESC LIMIT 50"
        )
    finally:
        db.close()
    return {
        "data": {
            "pending": [{"path": str(j)} for j in pending],
            "done": [
                {"path": row["transcript_path"], "extracted_at": row["extracted_at"]}
                for row in done_rows
            ],
        }
    }


@router.get("/extract/state")
def extract_state():
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        rows = db.fetchall(
            "SELECT transcript_path, transcript_mtime, extracted_at, status, "
            "last_byte_offset FROM extract_state ORDER BY extracted_at DESC LIMIT 100"
        )
        if not rows:
            return {"data": []}

        # Batch-fetch all rules for these transcripts
        transcript_paths = [row["transcript_path"] for row in rows]
        placeholders = ",".join("?" * len(transcript_paths))
        all_rules = db.fetchall(
            "SELECT id, short_id, status, trigger_canonical, trigger_canonical_zh, "
            "action_instruction, action_instruction_zh, severity, "
            "source_origin, transcript_ref, created_at, updated_at "
            f"FROM rules WHERE transcript_ref IN ({placeholders}) "
            "ORDER BY created_at DESC LIMIT 500",
            tuple(transcript_paths),
        )

        # Group rules by transcript_ref
        rules_by_transcript: dict[str, list] = {}
        rule_ids: list[str] = []
        for r in all_rules:
            rules_by_transcript.setdefault(r["transcript_ref"], []).append(r)
            rule_ids.append(r["id"])

        # Batch-fetch reviews and lineage for all rules (batch to stay under SQLite limit)
        # lineage query uses 2x params per batch, so keep batch_size < 999/2
        reviews_by_rule: dict[str, list] = {}
        lineage_by_rule: dict[str, list] = {}
        for batch in batched(rule_ids, batch_size=450):
            rid_ph = ",".join("?" * len(batch))
            all_reviews = db.fetchall(
                "SELECT rule_id, role, decision, scores, created_at "
                f"FROM rule_reviews WHERE rule_id IN ({rid_ph}) "
                "ORDER BY created_at",
                tuple(batch),
            )
            for rv in all_reviews:
                reviews_by_rule.setdefault(rv["rule_id"], []).append(rv)

            all_lineage = db.fetchall(
                "SELECT old_rule_id, new_rule_id, operation, reason, created_at "
                f"FROM rule_lineage WHERE old_rule_id IN ({rid_ph}) "
                f"OR new_rule_id IN ({rid_ph}) "
                "ORDER BY created_at",
                tuple(batch) + tuple(batch),
            )
            for ln in all_lineage:
                lineage_by_rule.setdefault(ln["old_rule_id"], []).append(ln)
                if ln["new_rule_id"] != ln["old_rule_id"]:
                    lineage_by_rule.setdefault(ln["new_rule_id"], []).append(ln)

        # Batch-fetch pipeline events using json_extract
        pipeline_by_transcript: dict[str, list] = {}
        all_pipeline_events = db.fetchall(
            "SELECT id, source, outcome, details, created_at "
            "FROM hook_events WHERE source = 'cold_pipeline' "
            f"AND json_extract(details, '$.transcript_ref') IN ({placeholders}) "
            "ORDER BY created_at",
            tuple(transcript_paths),
        )
        for ev in all_pipeline_events:
            details = _parse_details(ev["details"])
            tr = details.get("transcript_ref")
            if not tr:
                continue
            pipeline_by_transcript.setdefault(tr, []).append(
                {
                    "id": ev["id"],
                    "source": ev["source"],
                    "outcome": ev["outcome"],
                    "details": details,
                    "created_at": ev["created_at"],
                }
            )

        # Assemble result
        result = []
        for row in rows:
            tp = row["transcript_path"]
            rules_data = []
            for r in rules_by_transcript.get(tp, []):
                reviews = reviews_by_rule.get(r["id"], [])
                lineage = lineage_by_rule.get(r["id"], [])
                rules_data.append(
                    {
                        "id": r["id"],
                        "short_id": r["short_id"],
                        "status": r["status"],
                        "trigger_canonical": r["trigger_canonical"],
                        "trigger_canonical_zh": r["trigger_canonical_zh"],
                        "action_instruction": r["action_instruction"],
                        "action_instruction_zh": r["action_instruction_zh"],
                        "severity": r["severity"],
                        "source_origin": r["source_origin"],
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                        "reviews": [
                            {
                                "role": rv["role"],
                                "decision": rv["decision"],
                                "scores": loads_json(rv["scores"], {}),
                                "created_at": rv["created_at"],
                            }
                            for rv in reviews
                        ],
                        "lineage": [
                            {
                                "old_rule_id": ln["old_rule_id"],
                                "new_rule_id": ln["new_rule_id"],
                                "operation": ln["operation"],
                                "reason": ln["reason"],
                                "created_at": ln["created_at"],
                            }
                            for ln in lineage
                        ],
                    }
                )
            result.append(
                {
                    "transcript_path": tp,
                    "transcript_mtime": row["transcript_mtime"],
                    "extracted_at": row["extracted_at"],
                    "status": row["status"],
                    "last_byte_offset": row["last_byte_offset"],
                    "rules": rules_data,
                    "pipeline_events": pipeline_by_transcript.get(tp, []),
                }
            )
    finally:
        db.close()
    return {"data": result}


@router.get("/extract/transcript-events")
def transcript_events(transcript_path: str = Query(...)):
    """Get cold_pipeline and cli_extract events related to a specific transcript."""
    cfg = get_config()
    db = open_db(cfg.db_path)
    try:
        events = db.fetchall(
            "SELECT id, source, outcome, details, created_at "
            "FROM hook_events WHERE source IN ('cold_pipeline', 'cli_extract') "
            "AND json_extract(details, '$.transcript_ref') = ? "
            "ORDER BY created_at",
            (transcript_path,),
        )
        result = []
        for ev in events:
            result.append(
                {
                    "id": ev["id"],
                    "source": ev["source"],
                    "outcome": ev["outcome"],
                    "details": _parse_details(ev["details"]),
                    "created_at": ev["created_at"],
                }
            )
    finally:
        db.close()
    return {"events": result}
