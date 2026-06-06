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


def is_known_profile(model_id: str) -> bool:
    """Return whether a checked-in profile exists for the given model."""
    return model_id in CHECKED_IN_PROFILES


