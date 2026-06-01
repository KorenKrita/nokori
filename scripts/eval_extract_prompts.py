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
    wrap_untrusted,
)

DEFAULT_MODELS = (
    "deepseek-v4-flash,deepseek-v4-pro,gemini-3.5-flash,glm-5v-turbo,glm-5.1,MiniMax-M3,"
    "claude-sonnet-4-6,claude-opus-4-5"
)
QUICK_MODELS = (
    "deepseek-v4-flash,gemini-3.5-flash,claude-sonnet-4-6,claude-opus-4-5"
)
DEFAULT_JUDGE = "claude-opus-4-5"
DEFAULT_MIN_BYTES = 10_240
DEFAULT_MAX_BYTES = 180_000
PROD_COMPRESS_BUDGET_TOKENS = 30_000
PROD_EXTRACT_MAX_TOKENS = 3_000
PROD_EXTRACT_TIMEOUT_SEC = 60
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
        "temperature": 0.1,
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
        out["review"] = parsed
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
        if review.get("transcript_summary"):
            lines.append(f"**Summary:** {review['transcript_summary']}")
            lines.append(f"**Should extract:** `{review.get('should_extract')}`")
            lines.append("")

        for ex in sample.get("extractions", []):
            model = ex.get("model", "?")
            lines.append(f"### Extract: `{model}`")
            if ex.get("error"):
                lines.append(f"- API error: `{ex['error']}`")
                lines.append("")
                continue
            lines.append(
                f"- parse_ok={ex.get('parse_ok')} | "
                f"candidates={len(ex.get('candidates') or [])} | "
                f"{ex.get('elapsed')}s"
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

    if payload.get("global_synthesis"):
        gs = payload["global_synthesis"]
        lines.append("## Global synthesis (all samples)")
        lines.append("")
        if isinstance(gs, dict):
            for item in gs.get("deduped_improvements", []):
                lines.append(f"- {item}")
            if gs.get("draft_prompt_patch"):
                lines.append("")
                lines.append("### Draft patch")
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
        "Below are prompt_improvements collected from multiple transcript reviews.\n"
        "Deduplicate and merge into a short actionable list. Output JSON only:\n"
        '{"deduped_improvements":["..."], "draft_prompt_patch":"paragraph to add to EXTRACT_SYSTEM"}\n\n'
        + json.dumps(improvements, ensure_ascii=False, indent=2)
    )
    raw, _, err = _call_chat(
        base_url=base_url,
        api_key=api_key,
        model=judge_model,
        system=JUDGE_EXTRACT_SYSTEM,
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
    parser.add_argument("--judge-max-tokens", type=int, default=4000)
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help=f"Extract LLM timeout seconds (0 → production {PROD_EXTRACT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--judge-timeout",
        type=int,
        default=180,
        help="Judge LLM timeout seconds",
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

    picked = random.Random(args.seed).sample(pool, min(samples_n, len(pool)))

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

        sample: dict = {
            "path": str(path),
            "rel": rel,
            "file_bytes": path.stat().st_size,
            "compressed_chars": len(text),
            "compress_truncated": compress_truncated,
            "eval_capped": eval_capped,
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
                _log(f"    -> judge {sample['judge'].get('elapsed')}s")
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
            "max_chars_eval_cap": max_chars or None,
            "min_bytes": args.min_bytes,
            "max_bytes": args.max_bytes,
            "pool_size": len(pool),
        },
        "samples": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
