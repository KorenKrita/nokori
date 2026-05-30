from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

from ..config import Config
from ..db import open_db, total_rule_count
from ..search import embedding as embedding_search

RULE_COUNT_EMBED_WARN = 500


def _check_db(cfg: Config) -> tuple[str, str]:
    try:
        db = open_db(cfg.db_path)
        try:
            v = db.schema_version()
        finally:
            db.close()
        return ("ok", "rules.db readable")
    except Exception as e:
        return ("fail", str(e))


def _check_endpoint(label: str, base_url: str | None, model: str | None,
                    api_key: str | None, suffix: str) -> tuple[str, str]:
    if not base_url or not model:
        return ("skip", "not configured")
    url = f"{base_url.rstrip('/')}{suffix}"
    req = urllib.request.Request(url, method="HEAD")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return ("ok", f"{resp.status} {url}")
    except urllib.error.HTTPError as e:
        if 200 <= e.code < 500:
            return ("ok", f"{e.code} {url}")
        return ("fail", f"{e.code} {url}")
    except Exception as e:
        return ("fail", f"{type(e).__name__}: {e}")


def _check_rule_count(cfg: Config) -> tuple[str, str]:
    try:
        db = open_db(cfg.db_path)
        try:
            count = total_rule_count(db)
        finally:
            db.close()
    except Exception as e:
        return ("fail", str(e))

    embed_on = embedding_search.auto_enabled(cfg, count)
    if embed_on and count >= RULE_COUNT_EMBED_WARN:
        return (
            "warn",
            f"{count} searchable rules (active+dormant) — UserPromptSubmit embed "
            f"threshold uses per-prompt pool size; SessionStart warmup uses this "
            f"full count; consider fewer rules or disable embed above "
            f"~{RULE_COUNT_EMBED_WARN}",
        )
    return ("ok", f"{count} searchable rules")


def _check_settings_registered(cfg: Config) -> tuple[str, str]:
    from pathlib import Path

    settings = Path("~/.claude/settings.json").expanduser()
    if not settings.exists():
        return ("skip", f"{settings} missing")
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return ("fail", f"json parse error: {e}")
    hooks = data.get("hooks", {})
    needed = ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd")
    missing = []
    for evt in needed:
        spec = hooks.get(evt) or []
        if not any(
            "nokori" in (h.get("command", "")) for entry in spec for h in entry.get("hooks", [])
        ):
            missing.append(evt)
    if missing:
        return ("warn", f"missing: {','.join(missing)}")
    return ("ok", "registered")


def run(_args: argparse.Namespace, cfg: Config) -> int:
    rows = [
        ("db", *_check_db(cfg)),
        ("rules", *_check_rule_count(cfg)),
        ("settings.json", *_check_settings_registered(cfg)),
        ("llm",
         *_check_endpoint("llm", cfg.llm_base_url, cfg.llm_model,
                          cfg.llm_api_key, "/chat/completions")),
        ("embed",
         *_check_endpoint("embed", cfg.embed_base_url, cfg.embed_model,
                          cfg.embed_api_key, "/embeddings")),
    ]
    width = max(len(name) for name, *_ in rows)
    bad = False
    for name, status, detail in rows:
        marker = {"ok": "✓", "skip": "·", "warn": "!", "fail": "✗"}[status]
        print(f"  {marker} {name.ljust(width)}  {status:<4}  {detail}")
        if status == "fail":
            bad = True
    return 1 if bad else 0
