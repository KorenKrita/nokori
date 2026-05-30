from __future__ import annotations

import json
from pathlib import Path

from ..config import Config
from ..db import Db
from ..utils.logging import get_logger
from . import maintenance, promotion

log = get_logger("nokori.lifecycle.deferred")

_DEFERRED_DIR = "deferred"
_SHADOW_HITS = "shadow_hits.jsonl"
_DORMANT_REACTIVATE = "dormant_reactivate.jsonl"
_RULE_HITS = "rule_hits.jsonl"


def _deferred_dir(cfg: Config) -> Path:
    return cfg.data_dir / _DEFERRED_DIR


def _append_jsonl(cfg: Config, filename: str, payload: dict) -> None:
    cfg.ensure_dirs()
    path = _deferred_dir(cfg) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def enqueue_shadow_hit(cfg: Config, rule_id: str, project_id: str | None) -> None:
    _append_jsonl(
        cfg,
        _SHADOW_HITS,
        {"rule_id": rule_id, "project_id": project_id},
    )


def enqueue_dormant_reactivate(cfg: Config, rule_id: str) -> None:
    _append_jsonl(cfg, _DORMANT_REACTIVATE, {"rule_id": rule_id})


def enqueue_rule_hit(cfg: Config, rule_id: str, now: str) -> None:
    _append_jsonl(cfg, _RULE_HITS, {"rule_id": rule_id, "now": now})


def _consume_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning("deferred read failed %s: %s", path, e)
        return []
    try:
        path.unlink()
    except OSError:
        pass
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("deferred skip malformed line in %s", path.name)
    return out


def flush_deferred_writes(db: Db, cfg: Config) -> None:
    """Apply hook-deferred rule updates (SessionStart / maintenance)."""
    base = _deferred_dir(cfg)
    if not base.is_dir():
        return

    for entry in _consume_jsonl(base / _SHADOW_HITS):
        rule_id = entry.get("rule_id")
        if not rule_id:
            continue
        try:
            promotion.record_shadow_hit(db, rule_id, entry.get("project_id"))
        except Exception as e:
            log.warning("deferred shadow_hit failed rule=%s: %s", rule_id, e)

    seen_dormant: set[str] = set()
    for entry in _consume_jsonl(base / _DORMANT_REACTIVATE):
        rule_id = entry.get("rule_id")
        if not rule_id or rule_id in seen_dormant:
            continue
        seen_dormant.add(rule_id)
        try:
            maintenance.reactivate_dormant_on_retrieval_hot(db, rule_id)
        except Exception as e:
            log.warning("deferred dormant_reactivate failed rule=%s: %s", rule_id, e)

    hits: dict[str, str] = {}
    for entry in _consume_jsonl(base / _RULE_HITS):
        rule_id = entry.get("rule_id")
        now = entry.get("now")
        if rule_id and now:
            hits[rule_id] = now
    if hits:
        with db.transaction() as tx:
            for rule_id, now in hits.items():
                tx.execute(
                    "UPDATE rules SET hit_count = hit_count + 1, last_hit = ?, "
                    "updated_at = ? WHERE id = ?",
                    (now, now, rule_id),
                )
