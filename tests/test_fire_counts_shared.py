"""Characterization: single-rule and batch fire-count aggregation stay aligned."""

from nokori.lifecycle.evidence import compute_fire_counts


class _Row(dict):
    """sqlite3.Row-like mapping for aggregation helpers."""


def test_compute_fire_counts_tracks_sessions_and_strong_attribution():
    rows = [
        _Row(
            posthoc_label="observed_useful",
            posthoc_reason_code=None,
            posthoc_score=0.9,
            session_id="a",
        ),
        _Row(
            posthoc_label="observed_useful",
            posthoc_reason_code=None,
            posthoc_score=0.2,  # weak — not strong
            session_id="b",
        ),
        _Row(
            posthoc_label="irrelevant",
            posthoc_reason_code="irrelevant_not_applicable",
            posthoc_score=None,
            session_id="c",
        ),
    ]
    counts = compute_fire_counts(rows)
    assert counts["observed_useful"] == 2
    assert counts["observed_useful_strong"] == 1
    assert counts["distinct_observed_useful_sessions"] == 2
    assert counts["distinct_strong_useful_sessions"] == 1
    assert counts["irrelevant"] == 1
    assert counts["irrelevant_in_last_5"] == 1
    assert counts["reason_counts"]["irrelevant_not_applicable"] == 1


def test_legacy_null_score_counts_as_strong():
    rows = [
        _Row(
            posthoc_label="observed_useful",
            posthoc_reason_code=None,
            posthoc_score=None,
            session_id="legacy",
        ),
    ]
    counts = compute_fire_counts(rows)
    assert counts["observed_useful_strong"] == 1
    assert counts["distinct_strong_useful_sessions"] == 1
