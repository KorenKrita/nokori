from __future__ import annotations

import argparse
import os

from ..config import Config
from ..db import fetch_rules, open_db
from ..events.fire import count_evaluated_fire_events
from ..gate.blocker import select_gate_rules
from ..runtime.applicability import evaluate_applicability
from ..search.engine import RetrievalEngine
from ..utils.project import resolve_project_id


def _format_eligibility(r, pool_size: int) -> str:
    """Compute hard eligibility result for display."""
    result = evaluate_applicability(
        rule_status=r.rule.status,
        rule_severity=r.rule.severity,
        rule_first_observed_useful_at=r.rule.first_observed_useful_at,
        trigger_idf_sum=r.trigger_idf_sum,
        trigger_coverage=r.trigger_coverage,
        distinct_trigger_terms=r.distinct_trigger_terms,
        strong_variant_phrase_hit=r.strong_variant_phrase_hit,
        required_concepts_match=r.required_concepts_match,
        excluded_context_hit=r.excluded_context_hit,
        excluded_context_override_passed=r.excluded_context_override_passed,
        action_only_match=r.action_only_match,
        search_only_match=r.search_only_match,
        embedding_only_match=r.embedding_only_match,
        idf_stats_available=True,  # TODO: derive from actual IDF stats availability
        pool_size=pool_size,
        has_tool_input=False,
        false_positive_score=r.rule.false_positive_score,
    )
    return f"{result.decision.upper()} ({result.reason})"


def _format_posthoc_summary(db, rule_id: str) -> str:
    """One-line posthoc history summary."""
    counts = count_evaluated_fire_events(db, rule_id, window_days=30)
    total = counts.get("total_evaluated", 0)
    if total == 0:
        return "(no posthoc evaluations)"
    parts = []
    for label in ("observed_useful", "plausible_useful", "irrelevant", "harmful", "unclear"):
        n = counts.get(label, 0)
        if n > 0:
            parts.append(f"{label}={n}")
    return f"total={total} " + " ".join(parts)


def _print_scored_detail(r, db, pool_size: int) -> None:
    """Print fielded match details for a scored result."""
    # Fielded match evidence
    print(
        f"    trigger_idf_sum={r.trigger_idf_sum:.3f}  "
        f"trigger_coverage={r.trigger_coverage:.3f}  "
        f"matched_trigger={sorted(r.matched_trigger_tokens)}"
    )
    if r.matched_variant_tokens:
        print(f"    matched_variant={sorted(r.matched_variant_tokens)}")
    # Eligibility
    eligibility = _format_eligibility(r, pool_size)
    print(f"    eligibility: {eligibility}")
    # Ranking utility
    print(f"    ranking_utility={r.ranking_utility:.4f}")
    # Embedding profile bucket
    if r.embedding_profile_bucket:
        print(f"    embedding_profile_bucket={r.embedding_profile_bucket}")
    # Rule state and key scores
    print(
        f"    state={r.rule.status}  severity={r.rule.severity}  "
        f"usefulness={r.rule.observed_usefulness_score:.2f}  "
        f"fp={r.rule.false_positive_score:.2f}  "
        f"harmful={r.rule.harmful_score:.2f}"
    )
    # Posthoc summary
    posthoc = _format_posthoc_summary(db, r.rule.id)
    print(f"    posthoc(30d): {posthoc}")


def run(args: argparse.Namespace, cfg: Config) -> int:
    project_id = args.project
    if project_id is None:
        project_id = resolve_project_id(os.getcwd())

    db = open_db(cfg.db_path)
    try:
        if project_id is None:
            formal_rules = fetch_rules(db, statuses=("active", "trusted"), global_only=True)
        else:
            formal_rules = fetch_rules(db, statuses=("active", "trusted"), project_id=project_id)
        shadow_rules = (
            fetch_rules(db, statuses=("candidate", "suppressed"), project_id=project_id)
            if project_id and cfg.promotion_enabled
            else []
        )
        engine = RetrievalEngine(cfg, db)
        result = engine.retrieve(
            args.prompt,
            formal_rules,
            shadow_rules,
            interaction="cli",
        )
        hot, warm = result.hot, result.warm
        shadow_hot = result.shadow_hot
        pool_size = len(formal_rules)

        print(f"prompt        {args.prompt!r}")
        print(f"project_id    {project_id!r}")
        print(f"formal.pool   {len(formal_rules)} rules")
        print(f"shadow.pool   {len(shadow_rules)} rules")
        print(f"bm25.matches  {result.bm25_matches}")
        print(f"embed.mode    {result.embed_mode}")
        print()
        print(f"HOT  ({len(hot)}):")
        for r in hot:
            cos_str = f"  cos={r.cosine:.3f}" if r.cosine else ""
            print(f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  bm25={r.bm25_score:.4f}{cos_str}")
            print(f"    {r.rule.trigger_canonical[:80]}")
            _print_scored_detail(r, db, pool_size)
        print(f"WARM ({len(warm)}):")
        for r in warm:
            cos_str = f"  cos={r.cosine:.3f}" if r.cosine else ""
            print(f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  bm25={r.bm25_score:.4f}{cos_str}")
            _print_scored_detail(r, db, pool_size)

        gateable = select_gate_rules(hot)
        print()
        print(f"gate.would_block  {bool(gateable) and cfg.gate_enabled}")
        for r in gateable:
            print(f"  {r.rule.short_id}: {r.rule.action_instruction[:80]}")

        if shadow_hot:
            print()
            print(
                f"shadow_pool HOT ({len(shadow_hot)} would record hit, "
                f"embed={result.embed_mode}, not injected):"
            )
            for r in shadow_hot[:3]:
                print(
                    f"  {r.rule.short_id}  rrf={r.rrf_score:.4f}  "
                    f"bm25={r.bm25_score:.4f}  proj={r.rule.project_id}"
                )
        elif shadow_rules and cfg.promotion_enabled:
            print()
            print("shadow_pool HOT  (0 — no shadow HOT on this prompt)")
    finally:
        db.close()
    return 0
