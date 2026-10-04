"""The verification flag, over the post-level and user-level consensus."""

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


def test_the_flag_note_gives_the_distance_and_the_candidate_causes() -> None:
    note = _tri("Singapore", "Tokyo").notes[0]
    assert "5,311 km apart" in note
    assert "prompt to review the case, not a finding" in note
    for cause in ("one of the two predictions is wrong", "the post is about another place",
                  "travel post", "shared or compromised account", "misleading geotag"):
        assert cause in note


def test_the_near_miss_note_says_why_nothing_is_flagged() -> None:
    note = _tri("Singapore", "Tampines").notes[0]
    assert "near miss rather than a conflict" in note
    assert "no flag is raised" in note


def test_same_metro_does_not_flag() -> None:
    # Singapore against Tampines is about 15 km, a near-miss, not a conflict.
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


def test_an_abstaining_engine_is_not_counted_as_disagreeing() -> None:
    """It named nothing, so it should not sit in the agreement denominator."""
    from geolens.engines.base import Prediction
    from geolens.triangulator import triangulate

    answered = Prediction(city="Singapore", confidence=0.9, top_k=[("Singapore", 0.9)])
    abstained = Prediction(city="", confidence=0.0, top_k=[], abstain=True,
                           note="real:gazetteer (no toponym in text)")
    tri = triangulate(
        {"a": answered, "b": abstained}, engines={"a": "post", "b": "post"}
    )

    assert tri.consensus_city == "Singapore"
    assert tri.agreement_score == 1.0
