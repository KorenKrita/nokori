"""Maintainer-side benchmark runner for embedding profile generation.

Computes embeddings for all benchmark cases, calculates percentile statistics
per bucket, applies generation policy (warm_min, hot_min), validates results,
and returns a checked-in EmbeddingProfile.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nokori.search.embedding_profiles import (
    BucketThresholds,
    EmbeddingProfile,
    REQUIRED_BUCKETS,
    compute_hot_min,
    compute_warm_min,
)

# Minimum samples per required bucket for a valid profile.
MIN_BUCKET_SAMPLES: int = 3


def _percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile (0-100) of sorted values."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (p / 100.0) * (len(sorted_v) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_benchmark_cases(path: Path | str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_benchmark(
    embed_fn: Callable[[str], list[float]],
    benchmark_cases: list[dict[str, Any]] | Path | str,
    model_id: str,
    dimension: int,
    normalization: str = "cosine",
) -> EmbeddingProfile:
    """Run the benchmark suite and produce an EmbeddingProfile.

    Args:
        embed_fn: Function that takes a text string and returns a float vector.
        benchmark_cases: Either a list of case dicts or a path to the JSON file.
        model_id: Model identifier for the profile.
        dimension: Expected embedding dimension.
        normalization: Similarity metric (currently only "cosine" supported).

    Returns:
        EmbeddingProfile with computed thresholds for all buckets.
    """
    if isinstance(benchmark_cases, (str, Path)):
        benchmark_cases = _load_benchmark_cases(benchmark_cases)

    # Collect similarity scores per bucket and category.
    bucket_scores: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for case in benchmark_cases:
        bucket = case["bucket"]
        rule_trigger = case["rule_trigger"]

        trigger_vec = embed_fn(rule_trigger)

        for category in ("positive", "medium_positive", "near_miss", "negative"):
            texts = case.get(category, [])
            for text in texts:
                text_vec = embed_fn(text)
                score = _cosine(trigger_vec, text_vec)
                bucket_scores[bucket][category].append(score)

    # Compute per-bucket thresholds.
    computed_buckets: dict[str, BucketThresholds] = {}
    all_scores: dict[str, list[float]] = defaultdict(list)

    for bucket, categories in bucket_scores.items():
        positive = categories.get("positive", [])
        medium = categories.get("medium_positive", [])
        near_miss = categories.get("near_miss", [])
        negative = categories.get("negative", [])

        # Aggregate into "overall" pool.
        all_scores["positive"].extend(positive)
        all_scores["medium_positive"].extend(medium)
        all_scores["near_miss"].extend(near_miss)
        all_scores["negative"].extend(negative)

        sample_count = len(positive) + len(medium) + len(near_miss) + len(negative)

        positive_p10 = _percentile(positive, 10) if positive else 0.0
        medium_p10 = _percentile(medium, 10) if medium else 0.0
        medium_p50 = _percentile(medium, 50) if medium else 0.0
        near_miss_p95 = _percentile(near_miss, 95) if near_miss else 0.0
        near_miss_p99 = _percentile(near_miss, 99) if near_miss else 0.0
        negative_p99 = _percentile(negative, 99) if negative else 0.0

        warm_min = compute_warm_min(medium_p10, near_miss_p95)
        hot_min = compute_hot_min(positive_p10, near_miss_p99)

        computed_buckets[bucket] = BucketThresholds(
            positive_p10=positive_p10,
            medium_p10=medium_p10,
            medium_p50=medium_p50,
            near_miss_p95=near_miss_p95,
            near_miss_p99=near_miss_p99,
            negative_p99=negative_p99,
            warm_min=warm_min,
            hot_min=hot_min,
        )

    # Compute overall thresholds from aggregated scores.
    overall_positive_p10 = _percentile(all_scores["positive"], 10)
    overall_medium_p10 = _percentile(all_scores["medium_positive"], 10)
    overall_medium_p50 = _percentile(all_scores["medium_positive"], 50)
    overall_near_miss_p95 = _percentile(all_scores["near_miss"], 95)
    overall_near_miss_p99 = _percentile(all_scores["near_miss"], 99)
    overall_negative_p99 = _percentile(all_scores["negative"], 99)

    overall = BucketThresholds(
        positive_p10=overall_positive_p10,
        medium_p10=overall_medium_p10,
        medium_p50=overall_medium_p50,
        near_miss_p95=overall_near_miss_p95,
        near_miss_p99=overall_near_miss_p99,
        negative_p99=overall_negative_p99,
        warm_min=compute_warm_min(overall_medium_p10, overall_near_miss_p95),
        hot_min=compute_hot_min(overall_positive_p10, overall_near_miss_p99),
    )

    return EmbeddingProfile(
        model_id=model_id,
        profile_version="1",
        dimension=dimension,
        normalization=normalization,
        overall=overall,
        buckets=computed_buckets,
        embedding_only_allowed=False,
    )


def validate_benchmark_result(profile: EmbeddingProfile) -> list[str]:
    """Validate a benchmark-generated profile for internal consistency.

    Returns a list of error strings. Empty list means the profile passes.

    Failure conditions (from section 7.2 of the flywheel plan):
    - near_miss_p99 >= positive_p10 -> fail
    - negative_p99 >= medium_p10 -> fail
    - required bucket sample count < minimum -> fail
    """
    errors: list[str] = []

    # Overall separation checks.
    if profile.overall.near_miss_p99 >= profile.overall.positive_p10:
        errors.append(
            f"overall: near_miss_p99 ({profile.overall.near_miss_p99:.4f}) "
            f">= positive_p10 ({profile.overall.positive_p10:.4f})"
        )
    if profile.overall.negative_p99 >= profile.overall.medium_p10:
        errors.append(
            f"overall: negative_p99 ({profile.overall.negative_p99:.4f}) "
            f">= medium_p10 ({profile.overall.medium_p10:.4f})"
        )

    # Required bucket presence.
    for bucket_name in REQUIRED_BUCKETS:
        if bucket_name == "overall":
            continue
        if bucket_name not in profile.buckets:
            errors.append(
                f"required bucket '{bucket_name}' missing from profile"
            )

    # Per-bucket separation checks.
    for bucket_name, thresholds in profile.buckets.items():
        if thresholds.near_miss_p99 >= thresholds.positive_p10:
            errors.append(
                f"{bucket_name}: near_miss_p99 ({thresholds.near_miss_p99:.4f}) "
                f">= positive_p10 ({thresholds.positive_p10:.4f})"
            )
        if thresholds.negative_p99 >= thresholds.medium_p10:
            errors.append(
                f"{bucket_name}: negative_p99 ({thresholds.negative_p99:.4f}) "
                f">= medium_p10 ({thresholds.medium_p10:.4f})"
            )

    return errors
