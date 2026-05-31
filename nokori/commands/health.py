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


def _probe_openai_post(
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    *,
    path_suffix: str,
    payload: dict,
    timeout: int = 15,
) -> tuple[str, str]:
    """Minimal POST probe — matches LLMAdapter / EmbeddingClient (not HEAD)."""
    if not base_url or not model:
        return ("skip", "not configured")
    url = f"{base_url.rstrip('/')}{path_suffix}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return ("ok", f"{resp.status} {url}")
    except urllib.error.HTTPError as e:
        if 200 <= e.code < 300:
            return ("ok", f"{e.code} {url}")
        return ("fail", f"{e.code} {url}")
    except Exception as e:
        return ("fail", f"{type(e).__name__}: {e}")


def _check_llm_endpoint(cfg: Config) -> tuple[str, str]:
    return _probe_openai_post(
        cfg.llm_base_url,
        cfg.llm_model,
        cfg.llm_api_key,
        path_suffix="/chat/completions",
        payload={
            "model": cfg.llm_model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        },
        timeout=30,
    )


def _local_model_cached(cfg: Config) -> bool:
    """True when HuggingFace cache under data_dir/models has loadable weights."""
    from ..search.embedding import LOCAL_MODEL_NAME

    cache = cfg.data_dir / "models"
    hub = cache / f"models--sentence-transformers--{LOCAL_MODEL_NAME}"
    snapshots = hub / "snapshots"
    if not snapshots.is_dir():
        return False
    weight_names = ("model.safetensors", "pytorch_model.bin", "onnx/model.onnx")
    for snap in snapshots.iterdir():
        if not snap.is_dir():
            continue
        if any((snap / name).is_file() for name in weight_names):
            return True
    return False


def _embed_off_reason(cfg: Config, rule_count: int) -> str:
    if cfg.embed_enabled:
        if cfg.embed_base_url and cfg.embed_model:
            return ""
        if embedding_search._sentence_transformers_available():
            return ""
        return "embed.enabled=true but nokori[local-embed] not installed"
    if rule_count >= 20:
        if cfg.embed_base_url and cfg.embed_model:
            return ""
        if embedding_search._sentence_transformers_available():
            return ""
        return "rules>=20 but no remote embed.* and no local-embed package"
    return "embed.enabled=false and searchable rules<20 (auto threshold)"


def _check_embed(cfg: Config, rule_count: int) -> tuple[str, str]:
    """Report remote vs local mode, connectivity, model cache, and embed server."""
    from ..search import embed_ipc

    if not embedding_search.auto_enabled(cfg, rule_count):
        reason = _embed_off_reason(cfg, rule_count)
        return ("skip", f"off — {reason}")

    remote_configured = bool(cfg.embed_base_url and cfg.embed_model)
    if remote_configured:
        payload: dict = {"model": cfg.embed_model, "input": "ping"}
        if cfg.embed_dimensions and cfg.embed_dimensions > 0:
            payload["dimensions"] = cfg.embed_dimensions
        probe_status, probe_detail = _probe_openai_post(
            cfg.embed_base_url,
            cfg.embed_model,
            cfg.embed_api_key,
            path_suffix="/embeddings",
            payload=payload,
        )
        model = cfg.embed_model or "?"
        detail = (
            f"mode=remote; model={model}; endpoint={cfg.embed_base_url.rstrip('/')}/embeddings; "
            f"probe={probe_detail}"
        )
        return (probe_status, detail)

    # Local mode (enabled, no remote base_url+model)
    model_name = embedding_search.LOCAL_MODEL_NAME
    if not embedding_search._sentence_transformers_available():
        return (
            "fail",
            f"mode=local; model={model_name}; package=missing "
            "(pip install -e '.[local-embed]')",
        )

    cached = _local_model_cached(cfg)
    st = embed_ipc.server_status(cfg)
    cache_dir = cfg.data_dir / "models"
    parts = [
        "mode=local",
        f"model={model_name}",
        f"weights={'cached' if cached else 'not cached'} ({cache_dir})",
    ]
    if st["running"]:
        parts.append(f"server=running pid={st['pid']} socket={st['socket']}")
        return ("ok", "; ".join(parts))

    parts.append("server=stopped")
    if cached:
        return (
            "warn",
            "; ".join(parts)
            + " — run `nokori embed start` or open a new session (server_auto_start)",
        )
    return (
        "warn",
        "; ".join(parts)
        + " — first start downloads weights via `nokori embed start` or session-start",
    )


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


def _check_embedding_index_gaps(cfg: Config) -> tuple[str, str]:
    try:
        db = open_db(cfg.db_path)
    except Exception as e:
        return ("fail", str(e))
    try:
        count = total_rule_count(db)
        if not embedding_search.auto_enabled(cfg, count):
            return ("skip", "embedding not enabled for this library size")
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM rules r "
            "WHERE r.status IN ('active', 'dormant') "
            "AND NOT EXISTS (SELECT 1 FROM rule_embeddings e WHERE e.rule_id = r.id)"
        )
        missing = int(row["n"]) if row else 0
    finally:
        db.close()
    if missing:
        return (
            "warn",
            f"{missing} active/dormant rule(s) have no embedding rows — "
            "RRF uses BM25-only for those; run nokori extract or nokori edit to refresh",
        )
    return ("ok", "all searchable rules have embedding rows")


def _check_settings_registered(cfg: Config) -> tuple[str, str]:
    from pathlib import Path

    from .install import _settings_path

    settings = _settings_path()
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
            isinstance(entry, dict)
            and "nokori" in (h.get("command", ""))
            for entry in spec
            for h in entry.get("hooks", [])
        ):
            missing.append(evt)
    if missing:
        return ("warn", f"missing: {','.join(missing)}")
    return ("ok", "registered")


def run(_args: argparse.Namespace, cfg: Config) -> int:
    try:
        db = open_db(cfg.db_path)
        try:
            rule_count = total_rule_count(db)
        finally:
            db.close()
    except Exception:
        rule_count = 0

    rows = [
        ("db", *_check_db(cfg)),
        ("rules", *_check_rule_count(cfg)),
        ("embed.index", *_check_embedding_index_gaps(cfg)),
        ("settings.json", *_check_settings_registered(cfg)),
        ("llm", *_check_llm_endpoint(cfg)),
        ("embed", *_check_embed(cfg, rule_count)),
    ]
    width = max(len(name) for name, *_ in rows)
    bad = False
    for name, status, detail in rows:
        marker = {"ok": "✓", "skip": "·", "warn": "!", "fail": "✗"}[status]
        print(f"  {marker} {name.ljust(width)}  {status:<4}  {detail}")
        if status == "fail":
            bad = True
    return 1 if bad else 0
