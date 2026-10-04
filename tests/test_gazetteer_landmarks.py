"""Landmarks an operator entered are matchable, for onboarded places only.

A built-in place has no profile, so nothing here reaches the catalogue the
reported numbers were produced over.
"""

from __future__ import annotations

import pytest

from geolens.engines.base import GeolocateInput
from geolens.engines.gazetteer import LANDMARK_WEIGHT, GazetteerEngine
from geolens.onboarding.wizard import CityProfile, save_profile


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))


def _onboard(name: str, **fields) -> None:
    save_profile(CityProfile(name=name, **fields))


def test_a_landmark_matches_for_an_onboarded_place() -> None:
    _onboard("Cieunteung", landmarks=["Cieunteung bridge", "Citarum"])
    engine = GazetteerEngine(cities=["Cieunteung", "Bandung"], stub=False)

    pred = engine.predict(GeolocateInput(post="The Citarum has burst its banks again"))

    assert pred.city == "Cieunteung"
    assert pred.abstain is False
    assert "matched landmark: Citarum" in pred.evidence


def test_a_landmark_scores_below_a_name() -> None:
    _onboard("Cieunteung", landmarks=["Citarum"])
    _onboard("Sintang", landmarks=[])
    engine = GazetteerEngine(cities=["Cieunteung", "Sintang"], stub=False)

    pred = engine.predict(GeolocateInput(post="Citarum flooding reported from Sintang"))

    assert pred.city == "Sintang"
    scores = dict(pred.top_k)
    assert scores["Sintang"] > scores["Cieunteung"]
    assert scores["Cieunteung"] == pytest.approx(
        LANDMARK_WEIGHT / (1 + LANDMARK_WEIGHT)
    )


def test_the_evidence_names_both_places_a_text_matches() -> None:
    engine = GazetteerEngine(cities=["Singapore", "Tokyo"], stub=False)

    pred = engine.predict(GeolocateInput(post="Flew Singapore to Tokyo, Singapore was hot"))

    assert pred.city == "Singapore"
    assert "the text also names Tokyo" in pred.evidence


def test_a_built_in_place_has_no_landmarks_to_match() -> None:
    """Nothing is drafted for a built-in place, so its scoring is untouched."""
    engine = GazetteerEngine(cities=["Singapore", "Tokyo"], stub=False)

    pred = engine.predict(GeolocateInput(post="Marina Bay Sands is on fire"))

    assert pred.abstain is True
    assert pred.city == ""
