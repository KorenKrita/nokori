#!/usr/bin/env python3
"""Prompt-tuning eval: run extract models, then Opus judges outputs for prompt fixes.

Transcript pipeline matches production extract:
  read(path) → compress(budget_tokens=30000) → wrap_untrusted → LLM (max_tokens=3000, timeout=60)

No extra truncation unless you pass --max-chars (eval-only).

  python3 -u scripts/eval_extract_prompts.py --quick
  python3 -u scripts/eval_extract_prompts.py --samples 5

Outputs on ~/Desktop/:
  nokori-extract-eval-{timestamp}.json   — full raw outputs + judge JSON
  nokori-extract-eval-{timestamp}.md     — human-readable review
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nokori.config import Config
from nokori.constants import MAX_TRANSCRIPT_BYTES
from nokori.extract.compressor import compress
from nokori.extract.extractor import Candidate, _parse_candidates
from nokori.extract.reader import read
from nokori.llm.json_payload import parse_json_payload
from nokori.llm.prompts import (
    EXTRACT_SYSTEM,
    JUDGE_EXTRACT_SYSTEM,
    JUDGE_SYNTHESIZE_SYSTEM,
    wrap_untrusted,
)

DEFAULT_MODELS = "deepseek-v4-flash,gemini-3.5-flash"
QUICK_MODELS = "deepseek-v4-flash,gemini-3.5-flash"
DEFAULT_JUDGE = "claude-opus-4-5"
DEFAULT_MIN_BYTES = 10_240
DEFAULT_MAX_BYTES = 180_000
PROD_COMPRESS_BUDGET_TOKENS = 30_000
PROD_EXTRACT_MAX_TOKENS = 3_000
PROD_EXTRACT_TIMEOUT_SEC = 60
# Judge reviews all models + rubric scores; keep separate from extract hot-path timeout.
JUDGE_TIMEOUT_SEC = 300
# Matches EXTRACT_SYSTEM "at most 3 candidates"
MAX_EXPECTED_RULES = 3

# Judge rubric: five dimensions 0-100 (percent), total = rounded mean
SCORE_DIMS = ("format", "count", "trigger", "search_terms", "action")
SCORE_MAX_PER_DIM = 100
SCORE_MAX_TOTAL = 100
_COMPRESS_TRUNC_MARKER = "[transcript truncated: middle omitted]"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _discover_transcripts(
    *,
    limit_pool: int = 400,
    min_bytes: int = DEFAULT_MIN_BYTES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[Path]:
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return []
    entries: list[tuple[Path, int, float]] = []
    for p in base.rglob("*.jsonl"):
        if not p.is_file() or "/subagents/" in p.as_posix():
            continue
        st = p.stat()
        if min_bytes <= st.st_size <= max_bytes:
            entries.append((p, st.st_size, st.st_mtime))

    def _rank(entry: tuple[Path, int, float]) -> tuple[int, float]:
        p, _, mtime = entry
        name = p.as_posix().lower()
        boost = 0
        if "nokori" in name:
            boost += 4
        elif "coding" in name:
            boost += 2
        if "skill-evolve" in name or "revise-claude-md" in name:
            boost -= 4
        if "ccstatusline" in name:
            boost -= 2
        return (boost, mtime)

    entries.sort(key=_rank, reverse=True)
    return [p for p, _, _ in entries[:limit_pool]]


_TRIAGE_CACHE = Path("/tmp/nokori-eval-triage.json")

_TRIAGE_SYSTEM = """You quickly classify a compressed Claude Code conversation transcript.
Decide how many reusable behavioral rules a good extractor SHOULD find (software-engineering lessons only).

Output JSON only (no prose):
{"expected_rules": <integer 0-3>, "reason": "<one sentence>"}

Count distinct reusable behavioral lessons a good extractor should emit (max 3):
- 0: none — routine Q&A, no pushback; math/trivia with only generic retry ("对么", "再想") and no stated preference
- 1, 2, or 3: that exact number of separate corrections/preferences/lessons (do not collapse multiple lessons into a lower number)

Be strict: routine "do X" with no pushback = 0. Only count explicit user corrections or stated preferences."""


def _load_triage_cache() -> dict[str, dict]:
    if not _TRIAGE_CACHE.exists():
        return {}
    try:
        data = json.loads(_TRIAGE_CACHE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_triage_cache(cache: dict[str, dict]) -> None:
    _TRIAGE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_expected_rule_count(parsed: dict) -> int:
    """Exact count 0..MAX_EXPECTED_RULES (not a 2+ bucket)."""
    if "expected_rule_count" in parsed:
        n = parsed["expected_rule_count"]
    elif "expected_rules" in parsed:
        n = parsed["expected_rules"]
    elif "should_extract" in parsed:
        n = 1 if parsed["should_extract"] else 0
    else:
        n = 0
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return min(MAX_EXPECTED_RULES, max(0, n))


def _count_matches_expected(expected: int, actual: int) -> bool:
    return expected == actual


def _clamp_dim_score(value: object) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(SCORE_MAX_PER_DIM, n))


def _normalize_scores(raw: dict | None) -> dict[str, int]:
    scores = raw if isinstance(raw, dict) else {}
    out = {dim: _clamp_dim_score(scores.get(dim, 0)) for dim in SCORE_DIMS}
    out["total"] = round(sum(out[dim] for dim in SCORE_DIMS) / len(SCORE_DIMS))
    return out


def _per_model_by_name(review: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for pm in review.get("per_model") or []:
        if isinstance(pm, dict) and pm.get("model"):
            out[str(pm["model"])] = pm
    return out


def _normalize_judge_review(parsed: dict) -> dict:
    n = _normalize_expected_rule_count(parsed)
    parsed["expected_rule_count"] = n
    parsed["expected_rules"] = n
    parsed["should_extract"] = n > 0
    normalized_pms: list[dict] = []
    for pm in parsed.get("per_model") or []:
        if not isinstance(pm, dict):
            continue
        pm = dict(pm)
        pm["scores"] = _normalize_scores(pm.get("scores"))
        normalized_pms.append(pm)
    parsed["per_model"] = normalized_pms
    return parsed


def _format_scores_short(scores: dict[str, int] | None) -> str:
    if not scores:
        return "—"
    parts = [f"{scores.get(d, 0)}" for d in SCORE_DIMS]
    return "/".join(parts) + f"→{scores.get('total', 0)}%"


def _build_score_leaderboard(payload: dict) -> list[dict]:
    """Aggregate judge scores per extract model across samples."""
    models = list(payload.get("meta", {}).get("models") or [])
    buckets: dict[str, dict] = {
        m: {
            "model": m,
            "samples_scored": 0,
            "api_errors": 0,
            "count_hits": 0,
            "count_opportunities": 0,
            **{d: [] for d in SCORE_DIMS},
            "total": [],
        }
        for m in models
    }

    for sample in payload.get("samples", []):
        review = (sample.get("judge") or {}).get("review") or {}
        judge_n = review.get("expected_rule_count")
        if judge_n is not None:
            judge_n = _normalize_expected_rule_count(review)
        by_name = _per_model_by_name(review)

        for ex in sample.get("extractions", []):
            model = str(ex.get("model", ""))
            if model not in buckets:
                buckets[model] = {
                    "model": model,
                    "samples_scored": 0,
                    "api_errors": 0,
                    "count_hits": 0,
                    "count_opportunities": 0,
                    **{d: [] for d in SCORE_DIMS},
                    "total": [],
                }
            if ex.get("error"):
                buckets[model]["api_errors"] += 1
                continue
            if judge_n is not None:
                buckets[model]["count_opportunities"] += 1
                if _count_matches_expected(judge_n, len(ex.get("candidates") or [])):
                    buckets[model]["count_hits"] += 1
            pm = by_name.get(model)
            if not pm:
                continue
            scores = pm.get("scores")
            if not isinstance(scores, dict):
                continue
            buckets[model]["samples_scored"] += 1
            for d in SCORE_DIMS:
                buckets[model][d].append(int(scores.get(d, 0)))
            buckets[model]["total"].append(int(scores.get("total", 0)))

    rows: list[dict] = []
    for model in models:
        b = buckets.get(model)
        if not b:
            continue
        row = {
            "model": model,
            "samples_scored": b["samples_scored"],
            "api_errors": b["api_errors"],
        }
        if b["count_opportunities"]:
            row["count_hit_rate"] = b["count_hits"] / b["count_opportunities"]
        else:
            row["count_hit_rate"] = None
        for d in SCORE_DIMS:
            vals = b[d]
            row[f"avg_{d}"] = sum(vals) / len(vals) if vals else None
        totals = b["total"]
        row["avg_total"] = sum(totals) / len(totals) if totals else None
        rows.append(row)

    rows.sort(
        key=lambda r: (
            r["avg_total"] is not None,
            r["avg_total"] or -1,
            r["count_hit_rate"] or -1,
        ),
        reverse=True,
    )
    return rows


def _triage_one(
    path: Path,
    *,
    base_url: str,
    api_key: str | None,
    judge_model: str,
    budget_tokens: int,
    timeout: int,
) -> dict | None:
    """Classify one transcript. Returns {"expected_rules": 0..3, "reason": ...} or None on failure."""
    try:
        text = compress(read(path), budget_tokens=budget_tokens)
    except Exception:
        return None
    if not text.strip():
        return {"expected_rules": 0, "reason": "empty transcript"}
    # Use first 8000 chars for quick triage (saves tokens)
    snippet = text[:8000] if len(text) > 8000 else text
    raw, _, err = _call_chat(
        base_url=base_url,
        api_key=api_key,
        model=judge_model,
        system=_TRIAGE_SYSTEM,
        user=snippet,
        max_tokens=200,
        timeout=timeout,
    )
    if err or not raw:
        return None
    parsed = parse_json_payload(raw)
    if isinstance(parsed, dict) and (
        "expected_rules" in parsed
        or "expected_rule_count" in parsed
        or "should_extract" in parsed
    ):
        n = _normalize_expected_rule_count(parsed)
        parsed["expected_rules"] = n
        return parsed
    return None


def _cache_pool_counts(cache: dict[str, dict], pool: list[Path]) -> dict[int, int]:
    """Count cache entries per exact expected_rules (0..MAX_EXPECTED_RULES)."""
    counts: dict[int, int] = {i: 0 for i in range(MAX_EXPECTED_RULES + 1)}
    for p in pool:
        entry = cache.get(str(p))
        if entry is None:
            continue
        counts[_normalize_expected_rule_count(entry)] += 1
    return counts


def _run_triage(
    pool: list[Path],
    *,
    target_samples: int,
    base_url: str,
    api_key: str | None,
    judge_model: str,
    budget_tokens: int,
    timeout: int,
    max_workers: int,
    rng: random.Random,
) -> tuple[dict[str, dict], set[str]]:
    """Triage transcripts. Returns (cache, newly_classified).

    Skip triage entirely if cache already has >= 10x target_samples classified
    in the current pool. Otherwise classify uncached transcripts in batches
    until buckets are full enough.
    """
    cache = _load_triage_cache()
    newly_classified: set[str] = set()

    cached_in_pool = sum(1 for p in pool if str(p) in cache)
    skip_threshold = target_samples * 10

    if cached_in_pool >= skip_threshold:
        counts = _cache_pool_counts(cache, pool)
        _log(
            f"  triage: cache has {cached_in_pool} entries (>= {skip_threshold}), "
            f"skipping LLM calls. counts: {_format_rule_counts(counts)}"
        )
        return cache, set()

    need_per_bucket = {
        0: max(2, round(target_samples * 0.2)) + 2,
        1: max(2, round(target_samples * 0.6)) + 2,
        2: max(2, round(target_samples * 0.1)) + 1,
        3: max(1, round(target_samples * 0.1)) + 1,
    }

    def _fresh_bucket_counts() -> dict[int, int]:
        counts: dict[int, int] = {i: 0 for i in range(MAX_EXPECTED_RULES + 1)}
        for key in newly_classified:
            entry = cache.get(key)
            if entry is None:
                continue
            counts[_normalize_expected_rule_count(entry)] += 1
        return counts

    uncached = [p for p in pool if str(p) not in cache]
    if not uncached:
        _log(f"  triage: no uncached transcripts, using cache")
        return cache, set()

    rng.shuffle(uncached)
    batch_size = min(30, len(uncached))

    _log(f"  triage: {len(uncached)} uncached, classifying until buckets full ...")

    while uncached:
        batch = uncached[:batch_size]
        uncached = uncached[batch_size:]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _triage_one, p,
                    base_url=base_url,
                    api_key=api_key,
                    judge_model=judge_model,
                    budget_tokens=budget_tokens,
                    timeout=timeout,
                ): p
                for p in batch
            }
            for future in as_completed(futures):
                p = futures[future]
                try:
                    result = future.result()
                except Exception:
                    result = None
                if result is not None:
                    cache[str(p)] = result
                    newly_classified.add(str(p))

        _save_triage_cache(cache)
        counts = _fresh_bucket_counts()
        _log(f"    fresh {len(newly_classified)} → counts: {_format_rule_counts(counts)}")

        if all(counts[b] >= need_per_bucket[b] for b in need_per_bucket):
            break

    _log(f"  triage done: {len(newly_classified)} new classifications")
    return cache, newly_classified


def _format_rule_counts(counts: dict[int, int]) -> str:
    return " ".join(f"{k}={counts.get(k, 0)}" for k in range(MAX_EXPECTED_RULES + 1))


def _compute_bucket_targets(n: int) -> tuple[int, int, int, int]:
    """Compute (target_0, target_1, target_2, target_3) — exact rule counts."""
    if n <= 1:
        return (0, 1, 0, 0)
    if n == 2:
        return (0, 1, 1, 0)
    if n == 3:
        return (1, 1, 1, 0)
    target_0 = max(1, round(n * 0.2))
    target_3 = max(0, round(n * 0.1))
    target_2 = max(1, round(n * 0.15))
    target_1 = max(1, n - target_0 - target_2 - target_3)
    return (target_0, target_1, target_2, target_3)


def _pick_balanced_samples(
    pool: list[Path],
    triage_cache: dict[str, dict],
    n: int,
    rng: random.Random,
    newly_classified: set[str],
) -> list[Path]:
    """Pick samples with min guarantees and 20/60/20 ratio for n>=4.

    Prefer newly classified transcripts (from this run) over cached ones.
    Only fall back to cached entries when fresh ones can't fill the buckets.
    """
    fresh_buckets: dict[int, list[Path]] = {i: [] for i in range(MAX_EXPECTED_RULES + 1)}
    cached_buckets: dict[int, list[Path]] = {i: [] for i in range(MAX_EXPECTED_RULES + 1)}

    for p in pool:
        entry = triage_cache.get(str(p))
        if entry is None:
            continue
        bucket = _normalize_expected_rule_count(entry)
        if str(p) in newly_classified:
            fresh_buckets[bucket].append(p)
        else:
            cached_buckets[bucket].append(p)

    target_0, target_1, target_2, target_3 = _compute_bucket_targets(n)

    def _pick_from(fresh: list[Path], cached: list[Path], count: int) -> list[Path]:
        if count <= 0:
            return []
        rng.shuffle(fresh)
        picked = fresh[:count]
        if len(picked) < count:
            rng.shuffle(cached)
            picked.extend(cached[:count - len(picked)])
        return picked

    picked_0 = _pick_from(fresh_buckets[0], cached_buckets[0], target_0)
    picked_1 = _pick_from(fresh_buckets[1], cached_buckets[1], target_1)
    picked_2 = _pick_from(fresh_buckets[2], cached_buckets[2], target_2)
    picked_3 = _pick_from(fresh_buckets[3], cached_buckets[3], target_3)

    result = picked_0 + picked_1 + picked_2 + picked_3

    if len(result) < n:
        used = set(str(p) for p in result)
        remaining = [p for p in pool if str(p) not in used and str(p) in triage_cache]
        rng.shuffle(remaining)
        result.extend(remaining[:n - len(result)])

    rng.shuffle(result)

    _log(
        f"  balanced pick: 0={len(picked_0)} 1={len(picked_1)} 2={len(picked_2)} "
        f"3={len(picked_3)} total={len(result)} (target {n})"
    )
    return result[:n]


def _cap_transcript(text: str, max_chars: int) -> str:
    """Optional eval-only cap; production extract does not use this."""
    if max_chars <= 0:
        return text
    text = text.strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + "\n\n[... eval-only transcript cap ...]\n\n"
        + text[-half:]
    )


def _prepare_transcript(path: Path, *, budget_tokens: int) -> tuple[str, bool]:
    """Same pipeline as production: read(path) → compress(turns, budget_tokens=…)."""
    text = compress(read(path), budget_tokens=budget_tokens)
    truncated = _COMPRESS_TRUNC_MARKER in text
    return text, truncated


def _call_chat(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int,
) -> tuple[str | None, float, str | None]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, time.time() - t0, f"{type(e).__name__}: {e}"
    elapsed = time.time() - t0
    try:
        data = json.loads(body)
        if "error" in data:
            err = data["error"]
            msg = err.get("message", err) if isinstance(err, dict) else str(err)
            return None, elapsed, str(msg)
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        content = msg.get("content")
        if content is None:
            content = msg.get("reasoning_content") or ""
        if not (content or "").strip():
            return None, elapsed, "empty model content"
        return (content or "").strip(), elapsed, None
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return None, elapsed, f"bad response: {e}"


def _candidate_dict(c: Candidate) -> dict:
    return {
        "trigger": c.trigger,
        "trigger_variants": c.trigger_variants,
        "search_terms": c.search_terms,
        "behavior": c.behavior,
        "action": c.action,
        "rationale": c.rationale,
        "source_type": c.source_type,
        "confidence": c.confidence,
    }


def _run_extract(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    user: str,
    max_tokens: int,
    timeout: int,
) -> dict:
    raw, elapsed, err = _call_chat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system=EXTRACT_SYSTEM,
        user=user,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    row: dict = {"model": model, "elapsed": round(elapsed, 1)}
    if err:
        row["error"] = err
        return row
    row["raw_response"] = raw
    row["prose_prefix"] = bool(raw and not raw.lstrip().startswith(("[", "{")))
    cands, ok = _parse_candidates(raw or "")
    row["parse_ok"] = ok
    row["candidates"] = [_candidate_dict(c) for c in cands]
    return row


def _run_extracts_parallel(
    *,
    base_url: str,
    api_key: str | None,
    models: list[str],
    user: str,
    max_tokens: int,
    timeout: int,
    max_workers: int = 6,
) -> list[dict]:
    """Run extract calls for all models in parallel."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _run_extract,
                base_url=base_url,
                api_key=api_key,
                model=model,
                user=user,
                max_tokens=max_tokens,
                timeout=timeout,
            ): model
            for model in models
        }
        for future in as_completed(futures):
            model = futures[future]
            try:
                results[model] = future.result()
            except Exception as e:
                results[model] = {"model": model, "error": f"{type(e).__name__}: {e}"}
    return [results[m] for m in models]


def _format_extractions_for_judge(extractions: list[dict]) -> str:
    parts: list[str] = []
    for ex in extractions:
        parts.append(f"### Model: {ex['model']}")
        if ex.get("error"):
            parts.append(f"API error: {ex['error']}")
            parts.append("")
            continue
        parts.append(f"parse_ok={ex.get('parse_ok')} prose_prefix={ex.get('prose_prefix')}")
        parts.append("RAW OUTPUT:")
        parts.append(ex.get("raw_response") or "(empty)")
        parts.append("")
        parts.append("PARSED (after nokori extractor):")
        parts.append(json.dumps(ex.get("candidates") or [], ensure_ascii=False, indent=2))
        parts.append("")
    return "\n".join(parts)


def _run_judge(
    *,
    base_url: str,
    api_key: str | None,
    judge_model: str,
    transcript: str,
    extractions: list[dict],
    max_tokens: int,
    timeout: int,
) -> dict:
    user = (
        "## CURRENT EXTRACT_SYSTEM\n\n"
        f"{EXTRACT_SYSTEM}\n\n"
        "## TRANSCRIPT\n\n"
        f"{wrap_untrusted(transcript)}\n\n"
        "## MODEL OUTPUTS\n\n"
        f"{_format_extractions_for_judge(extractions)}"
    )
    raw, elapsed, err = _call_chat(
        base_url=base_url,
        api_key=api_key,
        model=judge_model,
        system=JUDGE_EXTRACT_SYSTEM,
        user=user,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    out: dict = {"model": judge_model, "elapsed": round(elapsed, 1)}
    if err:
        out["error"] = err
        return out
    out["raw_response"] = raw
    parsed = parse_json_payload(raw or "")
    if isinstance(parsed, dict):
        out["review"] = _normalize_judge_review(parsed)
    else:
        out["parse_error"] = "judge did not return a JSON object"
    return out


def _render_report(payload: dict) -> str:
    lines = [
        "# Nokori extract prompt eval",
        "",
        f"- Time: {payload['meta'].get('ts', '')}",
        f"- Judge: `{payload['meta'].get('judge_model', '')}`",
        f"- Extract models: {', '.join(payload['meta'].get('models', []))}",
        f"- Samples: {len(payload.get('samples', []))}",
        "",
        "> Purpose: tune **EXTRACT_SYSTEM**, not pick a winning extract LLM.",
        "",
    ]

    all_improvements: list[dict] = []

    for i, sample in enumerate(payload.get("samples", []), 1):
        lines.append(f"## Sample {i}: `{sample.get('rel', '')}`")
        lines.append("")
        judge = sample.get("judge") or {}
        review = judge.get("review") or {}
        triage_n = None
        if sample.get("triage") is not None:
            triage_n = _normalize_expected_rule_count(sample["triage"])
        judge_n = review.get("expected_rule_count")
        if judge_n is not None:
            judge_n = _normalize_expected_rule_count(review)
        if review.get("transcript_summary"):
            lines.append(f"**Summary:** {review['transcript_summary']}")
        if triage_n is not None or judge_n is not None:
            parts = []
            if triage_n is not None:
                parts.append(f"triage cache `{triage_n}`")
            if judge_n is not None:
                parts.append(f"judge `{judge_n}`")
            lines.append(f"**Expected rules:** {' | '.join(parts)}")
            if triage_n is not None and judge_n is not None and triage_n != judge_n:
                lines.append(f"- *(triage vs judge mismatch)*")
        elif review.get("should_extract") is not None:
            lines.append(f"**Should extract:** `{review.get('should_extract')}`")
        by_name = _per_model_by_name(review)
        if judge_n is not None:
            lines.append("")
            lines.append(
                "| Model | n | vs exp | total | "
                "fmt | cnt | trig | srch | act |"
            )
            lines.append(
                "|-------|---|--------|-------|"
                "-----|-----|------|------|-----|"
            )
            for ex in sample.get("extractions", []):
                model = ex.get("model", "?")
                if ex.get("error"):
                    lines.append(f"| `{model}` | err | — | — | — | — | — | — | — |")
                    continue
                n = len(ex.get("candidates") or [])
                ok = _count_matches_expected(judge_n, n)
                pm = by_name.get(str(model)) or {}
                sc = pm.get("scores") if isinstance(pm.get("scores"), dict) else {}
                def _c(dim: str) -> str:
                    return str(sc.get(dim, "—"))
                lines.append(
                    f"| `{model}` | {n} | {'ok' if ok else 'MISS'} | "
                    f"{sc.get('total', '—')} | "
                    f"{_c('format')} | {_c('count')} | {_c('trigger')} | "
                    f"{_c('search_terms')} | {_c('action')} |"
                )
            lines.append("")
            lines.append(
                "*Scores: each dimension 0-100 (percent); total = mean of five. "
                "fmt=format, cnt=count, trig=trigger, srch=search_terms, act=action.*"
            )
            lines.append("")

        for ex in sample.get("extractions", []):
            model = ex.get("model", "?")
            lines.append(f"### Extract: `{model}`")
            if ex.get("error"):
                lines.append(f"- API error: `{ex['error']}`")
                lines.append("")
                continue
            n_cand = len(ex.get("candidates") or [])
            match_s = ""
            if judge_n is not None:
                match_s = (
                    " match=ok"
                    if _count_matches_expected(judge_n, n_cand)
                    else " match=MISS"
                )
            lines.append(
                f"- parse_ok={ex.get('parse_ok')} | "
                f"candidates={n_cand} | "
                f"{ex.get('elapsed')}s{match_s}"
            )
            lines.append("")
            lines.append("<details><summary>Raw model output</summary>")
            lines.append("")
            lines.append("```")
            lines.append(ex.get("raw_response") or "")
            lines.append("```")
            lines.append("</details>")
            lines.append("")
            if ex.get("candidates"):
                lines.append("**Parsed:**")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(ex["candidates"], ensure_ascii=False, indent=2))
                lines.append("```")
                lines.append("")

        if review.get("per_model"):
            lines.append("### Judge: per-model diagnosis")
            lines.append("")
            for pm in review["per_model"]:
                lines.append(f"#### `{pm.get('model', '?')}`")
                sc = pm.get("scores")
                if isinstance(sc, dict):
                    lines.append(
                        f"- **Scores (%):** format={sc.get('format')} count={sc.get('count')} "
                        f"trigger={sc.get('trigger')} search_terms={sc.get('search_terms')} "
                        f"action={sc.get('action')} → **total={sc.get('total')}**"
                    )
                    if pm.get("extracted_well") is not None:
                        lines.append(f"- **extracted_well:** `{pm.get('extracted_well')}`")
                if pm.get("format_issues"):
                    lines.append("- **Format:** " + "; ".join(pm["format_issues"]))
                if pm.get("false_positives"):
                    lines.append("- **False positives:**")
                    for x in pm["false_positives"]:
                        lines.append(f"  - {x}")
                if pm.get("false_negatives"):
                    lines.append("- **False negatives (missed):**")
                    for x in pm["false_negatives"]:
                        lines.append(f"  - {x}")
                if pm.get("quality_notes"):
                    lines.append("- **Quality:** " + "; ".join(pm["quality_notes"]))
                lines.append("")

        if review.get("cross_model_patterns"):
            lines.append("### Cross-model patterns")
            for x in review["cross_model_patterns"]:
                lines.append(f"- {x}")
            lines.append("")

        if review.get("prompt_improvements"):
            lines.append("### Suggested EXTRACT_SYSTEM edits")
            for imp in review["prompt_improvements"]:
                if isinstance(imp, dict):
                    lines.append(f"- **Issue:** {imp.get('issue', '')}")
                    lines.append(f"  - **Change:** {imp.get('suggested_change', '')}")
                    lines.append(f"  - **Evidence:** {imp.get('evidence', '')}")
                    all_improvements.append(imp)
                else:
                    lines.append(f"- {imp}")
            lines.append("")

        if judge.get("error"):
            lines.append(f"*(Judge error: {judge['error']})*")
            lines.append("")
        lines.append("---")
        lines.append("")

    leaderboard = _build_score_leaderboard(payload)
    if leaderboard:
        lines.append("## Score leaderboard (judge, percent per sample)")
        lines.append("")
        lines.append(
            "Dimensions averaged over samples where the judge returned scores. "
            f"Each dimension 0-{SCORE_MAX_PER_DIM}%; total = mean of five (0-{SCORE_MAX_TOTAL}%)."
        )
        lines.append("")
        lines.append(
            "| Model | scored | err | count% | avg total | "
            "avg fmt | avg cnt | avg trig | avg srch | avg act |"
        )
        lines.append(
            "|-------|--------|-----|--------|-----------|"
            "--------|---------|----------|----------|---------|"
        )

        def _avg(v: float | None) -> str:
            return f"{v:.2f}" if v is not None else "—"

        for row in leaderboard:
            chr_ = row.get("count_hit_rate")
            chr_s = f"{chr_ * 100:.0f}%" if chr_ is not None else "—"
            lines.append(
                f"| `{row['model']}` | {row['samples_scored']} | {row['api_errors']} | "
                f"{chr_s} | **{_avg(row.get('avg_total'))}%** | "
                f"{_avg(row.get('avg_format'))} | {_avg(row.get('avg_count'))} | "
                f"{_avg(row.get('avg_trigger'))} | {_avg(row.get('avg_search_terms'))} | "
                f"{_avg(row.get('avg_action'))} |"
            )
        lines.append("")
        payload["score_leaderboard"] = leaderboard

    if payload.get("global_synthesis"):
        gs = payload["global_synthesis"]
        lines.append("## Global synthesis (all samples)")
        lines.append("")
        if isinstance(gs, dict):
            for item in gs.get("deduped_improvements", []):
                lines.append(f"- {item}")
            if gs.get("draft_prompt_patch"):
                lines.append("")
                lines.append("### Merge plan (vs current EXTRACT_SYSTEM)")
                lines.append("")
                lines.append("```")
                lines.append(gs["draft_prompt_patch"])
                lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _synthesize_global(
    *,
    base_url: str,
    api_key: str | None,
    judge_model: str,
    improvements: list[dict],
    timeout: int,
) -> dict | None:
    if not improvements:
        return None
    user = (
        "## CURRENT EXTRACT_SYSTEM\n\n"
        f"{EXTRACT_SYSTEM}\n\n"
        "## PROMPT_IMPROVEMENTS (from per-sample judges)\n\n"
        + json.dumps(improvements, ensure_ascii=False, indent=2)
    )
    raw, _, err = _call_chat(
        base_url=base_url,
        api_key=api_key,
        model=judge_model,
        system=JUDGE_SYNTHESIZE_SYSTEM,
        user=user,
        max_tokens=2000,
        timeout=timeout,
    )
    if err or not raw:
        return {"error": err or "empty"}
    parsed = parse_json_payload(raw)
    return parsed if isinstance(parsed, dict) else {"raw": raw}


def _load_resume(out_json: Path) -> dict | None:
    """Load partial results for resume."""
    if not out_json.exists():
        return None
    try:
        data = json.loads(out_json.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("samples"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval extract prompt (judge-driven)")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--samples", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--models", default="")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--no-synthesize", action="store_true", help="Skip final merge of improvements")
    parser.add_argument("--resume", action="store_true", help="Resume from partial output file")
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Pre-classify transcripts (exact expected_rules 0-3) for balanced sampling",
    )
    parser.add_argument("--triage-model", default="", help="Model for triage (default: same as --judge-model)")
    parser.add_argument(
        "--min-bytes",
        type=int,
        default=DEFAULT_MIN_BYTES,
        help=f"Min jsonl file size (default {DEFAULT_MIN_BYTES})",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Max jsonl file size (default {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--min-compressed",
        type=int,
        default=0,
        help="Skip if compress output shorter than this (0=only empty)",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="Eval-only extra cap after compress (0=disabled, production uses none)",
    )
    parser.add_argument(
        "--budget-tokens",
        type=int,
        default=PROD_COMPRESS_BUDGET_TOKENS,
        help=f"compress() token budget (production default {PROD_COMPRESS_BUDGET_TOKENS})",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=0,
        help=f"Extract LLM max_tokens (0 → production {PROD_EXTRACT_MAX_TOKENS})",
    )
    parser.add_argument(
        "--judge-max-tokens",
        type=int,
        default=8000,
        help="Judge max output tokens (7 models + scores need headroom; default 8000)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=PROD_EXTRACT_TIMEOUT_SEC,
        help=f"Extract LLM timeout seconds (default {PROD_EXTRACT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--judge-timeout",
        type=int,
        default=JUDGE_TIMEOUT_SEC,
        help=f"Judge/triage LLM timeout seconds only (default {JUDGE_TIMEOUT_SEC}; extract uses --timeout)",
    )
    parser.add_argument("--max-workers", type=int, default=6, help="Parallel extract workers")
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-md", default="")
    args = parser.parse_args()

    if args.quick:
        samples_n = args.samples or 2
        models_s = args.models or QUICK_MODELS
    else:
        samples_n = args.samples or 4
        models_s = args.models or DEFAULT_MODELS

    max_chars = args.max_chars
    max_tokens = args.max_tokens or PROD_EXTRACT_MAX_TOKENS
    extract_timeout = args.timeout or PROD_EXTRACT_TIMEOUT_SEC
    min_compressed = args.min_compressed

    cfg = Config.from_env()
    if not cfg.llm_base_url:
        _log("error: set [llm] base_url in ~/.nokori/config.toml")
        return 1

    models = [m.strip() for m in models_s.split(",") if m.strip()]
    pool = _discover_transcripts(
        min_bytes=args.min_bytes,
        max_bytes=args.max_bytes,
    )
    if not pool:
        _log(
            f"error: no transcripts in size range "
            f"[{args.min_bytes}, {args.max_bytes}] under ~/.claude/projects"
        )
        return 1

    rng = random.Random(args.seed)
    triage_model = args.triage_model or args.judge_model

    triage_cache: dict[str, dict] = {}
    newly_classified: set[str] = set()
    if args.triage:
        _log("Pre-classifying transcripts (triage phase) ...")
        triage_cache, newly_classified = _run_triage(
            pool,
            target_samples=samples_n,
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            judge_model=triage_model,
            budget_tokens=args.budget_tokens,
            timeout=args.judge_timeout,
            max_workers=args.max_workers,
            rng=rng,
        )
        picked = _pick_balanced_samples(pool, triage_cache, samples_n, rng, newly_classified)
    else:
        picked = rng.sample(pool, min(samples_n, len(pool)))

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_json = Path(args.out_json or str(Path.home() / "Desktop" / f"nokori-extract-eval-{ts}.json"))
    out_md = Path(args.out_md or str(Path.home() / "Desktop" / f"nokori-extract-eval-{ts}.md"))

    done_paths: set[str] = set()
    payload: dict

    if args.resume:
        existing = _load_resume(out_json)
        if existing:
            payload = existing
            done_paths = {s["path"] for s in payload.get("samples", []) if s.get("path")}
            _log(f"Resuming: {len(done_paths)} samples already done")
        else:
            payload = _make_meta(cfg, models, args, ts, max_chars, max_tokens, extract_timeout, pool)
    else:
        payload = _make_meta(cfg, models, args, ts, max_chars, max_tokens, extract_timeout, pool)

    def _save() -> None:
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out_md.write_text(_render_report(payload), encoding="utf-8")

    _log(f"extract models: {models}")
    _log(f"judge: {args.judge_model if not args.no_judge else '(disabled)'}")
    if not args.no_judge:
        _log(f"judge timeout: {args.judge_timeout}s (extract timeout: {extract_timeout}s)")
    _log(
        f"jsonl pool: {len(pool)} files in "
        f"[{args.min_bytes // 1024}KiB, {args.max_bytes // 1024}KiB]"
    )
    cap_note = f" + eval cap {max_chars} chars" if max_chars else ""
    _log(
        f"transcript: read(≤{MAX_TRANSCRIPT_BYTES // (1024*1024)}MiB) → "
        f"compress({args.budget_tokens} tokens){cap_note}"
    )
    _log(
        f"extract LLM: max_tokens={max_tokens} timeout={extract_timeout}s "
        f"(production-aligned)"
    )
    _log(f"parallel workers: {args.max_workers}")
    _log(f"samples: {len(picked)} seed={args.seed}")
    _log(f"output: {out_json.name}\n")

    all_improvements: list[dict] = []

    for idx, path in enumerate(picked, 1):
        if str(path) in done_paths:
            _log(f"[{idx}] SKIP (already done) {path.name}")
            continue
        try:
            text, compress_truncated = _prepare_transcript(
                path, budget_tokens=args.budget_tokens,
            )
        except Exception as e:
            _log(f"[{idx}] SKIP {path.name}: {e}")
            continue
        if not text.strip() or (min_compressed and len(text.strip()) < min_compressed):
            _log(f"[{idx}] SKIP {path.name}: compressed too short")
            continue
        eval_capped = False
        if max_chars:
            before = len(text)
            text = _cap_transcript(text, max_chars)
            eval_capped = len(text) < before
        rel = f"{path.parent.name}/{path.name}"
        trunc_flags = []
        if compress_truncated:
            trunc_flags.append("compress")
        if eval_capped:
            trunc_flags.append("eval-cap")
        trunc_s = f" truncated={','.join(trunc_flags)}" if trunc_flags else ""
        _log(f"[{idx}/{len(picked)}] {rel} ({len(text)} chars{trunc_s})")

        triage_info = triage_cache.get(str(path)) if args.triage else None
        sample: dict = {
            "path": str(path),
            "rel": rel,
            "file_bytes": path.stat().st_size,
            "compressed_chars": len(text),
            "compress_truncated": compress_truncated,
            "eval_capped": eval_capped,
            "triage": triage_info,
            "extractions": [],
        }
        user = wrap_untrusted(text)

        _log(f"  extracting with {len(models)} models (parallel) ...")
        extractions = _run_extracts_parallel(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            models=models,
            user=user,
            max_tokens=max_tokens,
            timeout=extract_timeout,
            max_workers=args.max_workers,
        )
        sample["extractions"] = extractions
        for ex in extractions:
            n = len(ex.get("candidates") or [])
            if ex.get("error"):
                _log(f"    {ex['model']}: ERROR {ex['error'][:80]}")
            else:
                _log(f"    {ex['model']}: {ex.get('elapsed')}s parse_ok={ex.get('parse_ok')} n={n}")
        _save()

        if not args.no_judge:
            _log(f"  judge ({args.judge_model}) ...")
            sample["judge"] = _run_judge(
                base_url=cfg.llm_base_url,
                api_key=cfg.llm_api_key,
                judge_model=args.judge_model,
                transcript=text,
                extractions=sample["extractions"],
                max_tokens=args.judge_max_tokens,
                timeout=args.judge_timeout,
            )
            review = (sample["judge"].get("review") or {})
            for imp in review.get("prompt_improvements") or []:
                if isinstance(imp, dict):
                    imp = {**imp, "_sample": rel}
                    all_improvements.append(imp)
            if sample["judge"].get("error"):
                _log(f"    -> judge ERROR {sample['judge']['error'][:80]}")
            else:
                jn = review.get("expected_rule_count")
                _log(
                    f"    -> judge {sample['judge'].get('elapsed')}s "
                    f"expected_rule_count={jn} should_extract={review.get('should_extract')}"
                )
                by_name = _per_model_by_name(review)
                for ex in sample["extractions"]:
                    if ex.get("error"):
                        continue
                    pm = by_name.get(str(ex.get("model", "")))
                    if not pm:
                        continue
                    sc = pm.get("scores") or {}
                    _log(
                        f"       {ex['model']}: total={sc.get('total', '?')}% "
                        f"({_format_scores_short(sc)})"
                    )
                for imp in (review.get("prompt_improvements") or [])[:2]:
                    if isinstance(imp, dict):
                        _log(f"       * {imp.get('issue', '')[:70]}")

        payload["samples"].append(sample)
        _save()
        _log("")

    if not args.no_judge and not args.no_synthesize and all_improvements:
        _log("global synthesis ...")
        payload["global_synthesis"] = _synthesize_global(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            judge_model=args.judge_model,
            improvements=all_improvements,
            timeout=args.judge_timeout,
        )
        _save()

    _log(f"Done.\n  JSON: {out_json}\n  Report: {out_md}")
    return 0


def _make_meta(cfg, models, args, ts, max_chars, max_tokens, extract_timeout, pool) -> dict:
    return {
        "meta": {
            "ts": ts,
            "base_url": cfg.llm_base_url,
            "models": models,
            "judge_model": None if args.no_judge else args.judge_model,
            "extract_prompt": EXTRACT_SYSTEM,
            "seed": args.seed,
            "align_production": max_chars == 0,
            "max_transcript_bytes": MAX_TRANSCRIPT_BYTES,
            "compress_budget_tokens": args.budget_tokens,
            "extract_max_tokens": max_tokens,
            "extract_timeout_sec": extract_timeout,
            "judge_timeout_sec": args.judge_timeout,
            "judge_max_tokens": args.judge_max_tokens,
            "max_chars_eval_cap": max_chars or None,
            "min_bytes": args.min_bytes,
            "max_bytes": args.max_bytes,
            "pool_size": len(pool),
        },
        "samples": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
