"""Cross-task (post vs. user) disagreement signal.

The signal compares the post-bucket consensus against the user-bucket
consensus and is gated on great-circle distance, so a same-metro near-miss
does not raise the same alarm as a cross-continent conflict.
"""

from __future__ import annotations

from geolens.engines.base import Prediction
from geolens.triangulator import triangulate

GRAN = {"post_eng": "post", "user_eng": "user"}


def _pred(city: str) -> Prediction:
    return Prediction(
        city=city, confidence=0.9, top_k=[(city, 0.9)], latency_ms=1.0, cost_usd=0.0, note="t"
    )


def _tri(post_city: str, user_city: str):
    return triangulate(
        {"post_eng": _pred(post_city), "user_eng": _pred(user_city)}, engines=GRAN
    )


def test_far_apart_flags() -> None:
    r = _tri("Singapore", "Tokyo")
    assert r.disagreement_flag is True
    assert r.disagreement_km and r.disagreement_km > 161
    assert r.disagreement_score == 1.0  # >= DISAGREEMENT_SCALE_KM
    assert r.post_consensus_city == "Singapore"
    assert r.user_consensus_city == "Tokyo"


def test_same_metro_does_not_flag() -> None:
    # Singapore vs. Tampines is ~15 km, a near-miss, not a conflict.
    r = _tri("Singapore", "Tampines")
    assert r.disagreement_flag is False
    assert r.disagreement_km is not None and r.disagreement_km < 161


def test_same_city_no_disagreement() -> None:
    r = _tri("Singapore", "Singapore")
    assert r.disagreement_flag is False
    assert r.disagreement_km == 0.0 or r.disagreement_km is None
    assert r.disagreement_score == 0.0


def test_missing_coordinate_flags_with_unknown_distance() -> None:
    # A city absent from the coordinate table (not onboarded) -> distance unknown.
    r = _tri("Singapore", "Nowhere City XYZ")
    assert r.disagreement_flag is True
    assert r.disagreement_km is None
    assert r.disagreement_score == 0.5
