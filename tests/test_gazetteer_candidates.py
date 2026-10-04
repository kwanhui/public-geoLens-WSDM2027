"""The gazetteer returns only the places the text actually named."""

from __future__ import annotations

from geolens.engines import GazetteerEngine
from geolens.engines.base import GeolocateInput
from geolens.ensemble import ensemble

CITIES = ["Singapore", "Tampines", "Bedok", "London", "Tokyo"]


def _engine() -> GazetteerEngine:
    return GazetteerEngine(stub=False, cities=CITIES, granularity="post")


def test_only_matched_places_are_returned() -> None:
    pred = _engine().predict(GeolocateInput(post="Snowed in again here in London"), k=5)

    assert pred.city == "London"
    assert [c for c, _ in pred.top_k] == ["London"]
    assert pred.abstain is False


def test_the_top_one_score_is_unchanged_by_dropping_the_padding() -> None:
    """The engine's own Acc@1 row depends on this staying the same."""
    pred = _engine().predict(GeolocateInput(post="London and London and Tokyo"), k=5)

    assert pred.city == "London"
    assert pred.confidence == 2 / 3
    assert pred.top_k == [("London", 2 / 3), ("Tokyo", 1 / 3)]


def test_an_abstention_returns_no_city() -> None:
    pred = _engine().predict(GeolocateInput(post="nothing here names a place"), k=5)

    assert pred.abstain is True
    assert pred.city == ""
    assert pred.confidence == 0.0
    assert pred.top_k == []
    assert "no toponym" in pred.note


def test_an_abstention_contributes_nothing_to_the_fusion() -> None:
    engine = _engine()
    abstained = engine.predict(GeolocateInput(post="nothing here names a place"), k=5)
    matched = engine.predict(GeolocateInput(post="Tokyo"), k=5)

    result = ensemble(
        {"gazetteer_post": abstained, "other_post": matched},
        {"gazetteer_post": "post", "other_post": "post"},
        target="post",
    )
    assert result is not None
    assert result.contributing_engines == ["other_post"]
    assert result.consensus_city == "Tokyo"


def test_an_unmatched_city_stays_out_of_the_fused_list() -> None:
    pred = _engine().predict(GeolocateInput(post="Snowed in again here in London"), k=5)
    result = ensemble({"g": pred}, {"g": "post"}, target="post", k=5)

    assert result is not None
    assert [c for c, _ in result.top_k] == ["London"]


def test_the_gazetteer_runs_for_real_in_placeholder_mode(monkeypatch) -> None:
    """It needs no key, so a keyless clone should see one engine working."""
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("GEOLENS_STUB_RULE_BASED", raising=False)
    engine = GazetteerEngine(cities=CITIES, granularity="post")

    assert engine.stub is False
    pred = engine.predict(GeolocateInput(post="Snowed in again here in London"), k=5)
    assert pred.city == "London"
    assert pred.mode == "real"


def test_a_test_only_switch_still_forces_the_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_STUB_RULE_BASED", "1")
    engine = GazetteerEngine(cities=CITIES, granularity="post")

    assert engine.stub is True
    assert engine.predict(GeolocateInput(post="London"), k=5).mode == "stub"


def test_an_engine_that_needs_a_key_still_placeholders(monkeypatch) -> None:
    from geolens.engines import ContrastGeoEngine

    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    assert ContrastGeoEngine(cities=CITIES).stub is True
