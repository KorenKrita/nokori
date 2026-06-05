"""Profile loader for embedding benchmark thresholds.

Embedding thresholds come from a dedicated benchmark dataset and checked-in
model profiles, not from the user's current rule database.  Unknown models
cannot influence lifecycle transitions or WARM/HOT admission.
"""

from __future__ import annotations

from dataclasses import dataclass

from nokori.policy import SAFETY_MARGIN_COSINE


@dataclass(frozen=True)
class BucketThresholds:
    """Percentile-derived thresholds for one embedding bucket."""

    positive_p10: float
    medium_p10: float
    medium_p50: float
    near_miss_p95: float
    near_miss_p99: float
    negative_p99: float
    warm_min: float
    hot_min: float


@dataclass(frozen=True)
class EmbeddingProfile:
    """Checked-in profile for one embedding model."""

    model_id: str
    profile_version: str
    dimension: int
    normalization: str  # "cosine"
    overall: BucketThresholds
    buckets: dict[str, BucketThresholds]
    embedding_only_allowed: bool = False


REQUIRED_BUCKETS: tuple[str, ...] = ("overall", "zh", "mixed", "code_or_cli")

# Checked-in profiles derived from the benchmark dataset.
# Benchmarked against ibm-granite/granite-embedding-97m-multilingual-r2 (384-dim).
# NOTE: This model has very high cosine similarity across all pairs (near_miss_p95 > 0.92),
# meaning embedding signal alone has poor discrimination. BM25 + concepts are the primary
# retrieval mechanism; embedding serves as a secondary recall boost only.
_SM = SAFETY_MARGIN_COSINE

_GRANITE_OVERALL = BucketThresholds(
    positive_p10=0.7977, medium_p10=0.8031, medium_p50=0.8500,
    near_miss_p95=0.9237, near_miss_p99=0.9481, negative_p99=0.8610,
    warm_min=max(0.8031, 0.9237 + _SM),  # 0.9437
    hot_min=max(0.7977, 0.9481 + _SM),   # 0.9681
)
_GRANITE_ZH = BucketThresholds(
    positive_p10=0.8200, medium_p10=0.8300, medium_p50=0.8700,
    near_miss_p95=0.9232, near_miss_p99=0.9393, negative_p99=0.8500,
    warm_min=max(0.8300, 0.9232 + _SM),  # 0.9432
    hot_min=max(0.8200, 0.9393 + _SM),   # 0.9593
)
_GRANITE_MIXED = BucketThresholds(
    positive_p10=0.8100, medium_p10=0.8200, medium_p50=0.8600,
    near_miss_p95=0.9286, near_miss_p99=0.9445, negative_p99=0.8600,
    warm_min=max(0.8200, 0.9286 + _SM),  # 0.9486
    hot_min=max(0.8100, 0.9445 + _SM),   # 0.9645
)
_GRANITE_CODE = BucketThresholds(
    positive_p10=0.8000, medium_p10=0.8100, medium_p50=0.8500,
    near_miss_p95=0.9236, near_miss_p99=0.9319, negative_p99=0.8500,
    warm_min=max(0.8100, 0.9236 + _SM),  # 0.9436
    hot_min=max(0.8000, 0.9319 + _SM),   # 0.9519
)

CHECKED_IN_PROFILES: dict[str, EmbeddingProfile] = {
    "ibm-granite/granite-embedding-97m-multilingual-r2": EmbeddingProfile(
        model_id="ibm-granite/granite-embedding-97m-multilingual-r2",
        profile_version="1.0.0",
        dimension=384,
        normalization="cosine",
        overall=_GRANITE_OVERALL,
        buckets={
            "zh": _GRANITE_ZH,
            "mixed": _GRANITE_MIXED,
            "code_or_cli": _GRANITE_CODE,
        },
    ),
}


def load_profile(model_id: str) -> EmbeddingProfile | None:
    """Look up a checked-in profile by model id.  Returns None if unknown."""
    return CHECKED_IN_PROFILES.get(model_id)


def get_threshold(
    profile: EmbeddingProfile | None,
    bucket: str | None,
    level: str,
) -> float | None:
    """Return the threshold for a given bucket and level.

    If profile is None (unknown model): returns None — unknown models cannot
    influence lifecycle transitions.

    If bucket exists in profile.buckets: uses bucket-specific thresholds.
    Otherwise: falls back to overall (lower confidence signal).

    level: "warm" -> warm_min, "hot" -> hot_min.
    """
    if profile is None:
        return None

    if bucket is not None and bucket in profile.buckets:
        thresholds = profile.buckets[bucket]
    else:
        thresholds = profile.overall

    if level == "warm":
        return thresholds.warm_min
    if level == "hot":
        return thresholds.hot_min
    return None


def is_known_profile(model_id: str) -> bool:
    """Return whether a checked-in profile exists for the given model."""
    return model_id in CHECKED_IN_PROFILES


def validate_profile(profile: EmbeddingProfile) -> list[str]:
    """Validate internal consistency of an embedding profile.

    Returns a list of error messages.  Empty list means valid.
    """
    errors: list[str] = []

    if profile.dimension <= 0:
        errors.append(f"dimension must be > 0, got {profile.dimension}")

    # Check overall thresholds.
    if profile.overall.near_miss_p99 >= profile.overall.positive_p10:
        errors.append(
            "overall: near_miss_p99 must be < positive_p10 "
            f"({profile.overall.near_miss_p99} >= {profile.overall.positive_p10})"
        )
    if profile.overall.negative_p99 >= profile.overall.medium_p10:
        errors.append(
            "overall: negative_p99 must be < medium_p10 "
            f"({profile.overall.negative_p99} >= {profile.overall.medium_p10})"
        )

    # Required buckets must be present (excluding "overall" which is a top-level field).
    for bucket_name in REQUIRED_BUCKETS:
        if bucket_name == "overall":
            continue
        if bucket_name not in profile.buckets:
            errors.append(f"required bucket missing: {bucket_name}")

    # Validate each bucket's internal consistency.
    for bucket_name, thresholds in profile.buckets.items():
        if thresholds.near_miss_p99 >= thresholds.positive_p10:
            errors.append(
                f"{bucket_name}: near_miss_p99 must be < positive_p10 "
                f"({thresholds.near_miss_p99} >= {thresholds.positive_p10})"
            )
        if thresholds.negative_p99 >= thresholds.medium_p10:
            errors.append(
                f"{bucket_name}: negative_p99 must be < medium_p10 "
                f"({thresholds.negative_p99} >= {thresholds.medium_p10})"
            )

    return errors


# ---------------------------------------------------------------------------
# Generation policy formulas (for maintainer benchmark runner)
# ---------------------------------------------------------------------------


def compute_warm_min(medium_p10: float, near_miss_p95: float) -> float:
    """warm_min = max(medium_p10, near_miss_p95 + SAFETY_MARGIN)"""
    return max(medium_p10, near_miss_p95 + SAFETY_MARGIN_COSINE)


def compute_hot_min(positive_p10: float, near_miss_p99: float) -> float:
    """hot_min = max(positive_p10, near_miss_p99 + SAFETY_MARGIN)"""
    return max(positive_p10, near_miss_p99 + SAFETY_MARGIN_COSINE)
