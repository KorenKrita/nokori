from __future__ import annotations

import argparse
import json

from ..config import Config
from ..db import fetch_rule_by_short_id, open_db
from ..errors import NokoriError
from ..events.fire import count_evaluated_fire_events


def _json_list(raw: str) -> list:
    """Parse a JSON list field, returning [] on failure."""
    try:
        val = json.loads(raw) if raw else []
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = None
    try:
        db = open_db(cfg.db_path)
        rule = fetch_rule_by_short_id(db, args.short_id)
        if rule is None:
            raise NokoriError(f"no rule with short_id {args.short_id!r}")

        # Posthoc history
        posthoc_counts = count_evaluated_fire_events(db, rule.id, window_days=30)

        # Archived fingerprint summary
        archived_fp = None
        if rule.status == "archived":
            archived_fp = {
                "reason": rule.archived_reason,
                "replacement_id": rule.replacement_id,
            }
    finally:
        if db is not None:
            db.close()

    # --- Identity ---
    print(f"id              {rule.id}")
    print(f"short_id        {rule.short_id}")
    print(f"status          {rule.status}")
    print(f"severity        {rule.severity}")
    print(f"source_origin   {rule.source_origin}")
    print(f"activation_origin {rule.activation_origin or '-'}")
    print(f"project_scope   {rule.project_scope}")
    print(f"project_id      {rule.project_id or '-'}")

    # --- Version fields ---
    print()
    print(f"schema_version         {rule.schema_version}")
    print(f"rule_version           {rule.rule_version}")
    print(f"runtime_policy_version {rule.runtime_policy_version or '-'}")
    print(f"created_by_pipeline    {rule.created_by_pipeline_version or '-'}")

    # --- Trigger ---
    print()
    print(f"trigger_canonical: {rule.trigger_canonical}")
    if rule.trigger_canonical_zh:
        print(f"trigger_canonical_zh: {rule.trigger_canonical_zh}")

    variants = _json_list(rule.trigger_variants)
    if variants:
        print("variants:")
        for v in variants:
            text = v.get("text") if isinstance(v, dict) else v
            print(f"  {text}")
    variants_zh = rule.trigger_variants_zh
    if variants_zh:
        print("variants_zh:")
        for v in variants_zh:
            print(f"  {v}")

    # --- Structured fields ---
    concepts = _json_list(rule.concepts)
    if concepts:
        print(f"concepts: {concepts}")

    required_groups = _json_list(rule.required_concept_groups)
    if required_groups:
        print(f"required_concept_groups: {required_groups}")

    excluded_contexts = _json_list(rule.excluded_contexts)
    if excluded_contexts:
        print(f"excluded_contexts: {excluded_contexts}")

    # --- Action ---
    print()
    print(f"action_instruction: {rule.action_instruction}")
    if rule.action_instruction_zh:
        print(f"action_instruction_zh: {rule.action_instruction_zh}")
    if rule.allowed_behavior:
        print(f"allowed_behavior: {rule.allowed_behavior}")
    if rule.forbidden_behavior:
        print(f"forbidden_behavior: {rule.forbidden_behavior}")

    # --- Scope ---
    if rule.domain_tags:
        print(f"domain_tags: {rule.domain_tags}")
    if rule.tool_tags:
        print(f"tool_tags: {rule.tool_tags}")
    if rule.path_patterns:
        print(f"path_patterns: {rule.path_patterns}")
    if rule.search_terms:
        print("search_terms:")
        for lang, items in rule.search_terms.items():
            print(f"  {lang}: {', '.join(items)}")

    # --- Score breakdown ---
    print()
    print("scores:")
    print(f"  quality              {rule.quality_score:.3f}")
    print(f"  evidence_support     {rule.evidence_support_score:.3f}")
    print(f"  specificity          {rule.specificity_score:.3f}")
    print(f"  retrieval_readiness  {rule.retrieval_readiness_score:.3f}")
    print(f"  observed_usefulness  {rule.observed_usefulness_score:.3f}")
    print(f"  plausible_usefulness {rule.plausible_usefulness_score:.3f}")
    print(f"  false_positive       {rule.false_positive_score:.3f}")
    print(f"  harmful              {rule.harmful_score:.3f}")

    # --- Lifecycle history ---
    print()
    print("lifecycle:")
    print(f"  first_observed_useful_at {rule.first_observed_useful_at or '-'}")
    print(f"  trusted_at               {rule.trusted_at or '-'}")
    print(f"  suppressed_at            {rule.suppressed_at or '-'}")
    print(f"  created_at               {rule.created_at}")
    print(f"  updated_at               {rule.updated_at}")

    # --- Posthoc history ---
    total_eval = posthoc_counts.get("total_evaluated", 0)
    if total_eval > 0:
        print()
        print(f"posthoc(30d): total={total_eval}")
        for label in ("observed_useful", "plausible_useful", "irrelevant", "harmful", "unclear"):
            n = posthoc_counts.get(label, 0)
            if n > 0:
                print(f"  {label}: {n}")

    # --- Archived fingerprint ---
    if archived_fp:
        print()
        print("archived:")
        print(f"  reason:         {archived_fp['reason'] or '-'}")
        print(f"  replacement_id: {archived_fp['replacement_id'] or '-'}")

    return 0
