"""What a per-row export carries: coordinates, per-engine places and distances."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.engines._coords import coords_for
from geolens.geo import haversine_km
from geolens.places import place_id
from geolens.ui.server import create_app

ROWS = [
    {
        "id": "sg-1",
        "post": "Queue at the Bedok hawker centre again",
        "ground_truth_city": "Bedok",
    },
    {
        "id": "osint-1",
        "post": "Fire at Marina Bay Sands",
        "user_posts": ["Ramen in Shibuya again", "Tokyo is cold tonight"],
        "ground_truth_city": "Singapore",
        "ground_truth_user_city": "Tokyo",
    },
]


@pytest.fixture
def body(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        return client.post("/eval", json={"inputs": ROWS}).json()


def test_every_engine_answer_carries_its_identifier_and_point(body) -> None:
    for row in body["rows"]:
        for pred in row["per_engine"].values():
            if not pred["city"]:
                assert pred["place_id"] is None
                assert pred["place_coordinates"] is None
                continue
            assert pred["place_id"] == place_id(pred["city"])
            expected = coords_for(pred["city"])
            assert pred["place_coordinates"] == [expected[0], expected[1]]


def test_an_engine_answer_carries_its_distance_from_the_rows_truth(body) -> None:
    row = next(r for r in body["rows"] if r["id"] == "sg-1")
    scored = [p for p in row["per_engine"].values() if p["error_km"] is not None]
    assert scored, "no engine was scored against the truth"
    for pred in scored:
        expected = haversine_km(coords_for(pred["city"]), coords_for("Bedok"))
        assert pred["error_km"] == pytest.approx(round(expected, 1))
        assert pred["within_161km"] is (expected <= 161.0)


def test_the_distance_is_measured_against_the_truth_for_the_engines_level(body) -> None:
    """A disagree row has two truths, and each engine is scored on its own."""
    row = next(r for r in body["rows"] if r["id"] == "osint-1")
    post_pred = row["per_engine"]["gazetteer_post"]
    user_pred = row["per_engine"]["gazetteer_user"]
    if post_pred["city"]:
        assert post_pred["error_km"] == pytest.approx(
            round(haversine_km(coords_for(post_pred["city"]), coords_for("Singapore")), 1)
        )
    if user_pred["city"]:
        assert user_pred["error_km"] == pytest.approx(
            round(haversine_km(coords_for(user_pred["city"]), coords_for("Tokyo")), 1)
        )


def test_a_distance_is_reported_to_one_decimal(body) -> None:
    for row in body["rows"]:
        for pred in row["per_engine"].values():
            if pred["error_km"] is not None:
                assert round(pred["error_km"], 1) == pred["error_km"]


def test_an_engine_that_named_no_place_has_no_distance(body) -> None:
    for row in body["rows"]:
        for pred in row["per_engine"].values():
            if not pred["city"]:
                assert pred["error_km"] is None
                assert pred["within_161km"] is None


def test_a_row_without_truth_has_no_distances(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        out = client.post("/batch_predict", json={
            "inputs": [{"id": "1", "post": "Queue at Bedok"}]
        }).json()
    for pred in out["rows"][0]["per_engine"].values():
        assert pred["error_km"] is None
        assert pred["within_161km"] is None


def test_the_truth_carries_its_identifier(body) -> None:
    row = next(r for r in body["rows"] if r["id"] == "osint-1")
    assert row["ground_truth_place_id"] == place_id("Singapore")
    assert row["ground_truth_user_place_id"] == place_id("Tokyo")


def test_the_consensus_pair_carries_its_identifiers(body) -> None:
    row = next(r for r in body["rows"] if r["id"] == "osint-1")
    tri = row["triangulation"]
    for name_field, id_field in (
        ("post_consensus_city", "post_consensus_place_id"),
        ("user_consensus_city", "user_consensus_place_id"),
    ):
        if tri[name_field]:
            assert tri[id_field] == place_id(tri[name_field])
        else:
            assert tri[id_field] is None


def test_the_places_list_covers_every_place_the_run_names(body) -> None:
    named = set()
    for row in body["rows"]:
        named.update(p["city"] for p in row["per_engine"].values() if p["city"])
        named.update(e["consensus_city"] for e in row["ensembles"].values())
        for field in ("ground_truth_city", "ground_truth_user_city"):
            if row[field]:
                named.add(row[field])
    listed = {p["name"] for p in body["places"]}
    assert named <= listed


def test_a_listed_place_says_what_its_point_is(body) -> None:
    by_name = {p["name"]: p for p in body["places"]}
    bedok = by_name["Bedok"]
    assert bedok["place_id"] == place_id("Bedok")
    assert bedok["source"] == "built-in"
    assert bedok["feature_type"] == "estate"
    assert bedok["centroid_source"] == "seed-approximate"

    tokyo = by_name["Tokyo"]
    assert tokyo["feature_type"] == "city"


def test_the_catalogue_says_what_each_stored_point_is(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        out = client.get("/catalogue").json()
    by_name = {p["name"]: p for p in out["places"]}

    assert by_name["Singapore"]["feature_type"] == "country"
    assert by_name["Tengah Plantation Crescent"]["feature_type"] == "street"
    assert by_name["Tampines"]["feature_type"] == "estate"
    assert by_name["Los Angeles"]["feature_type"] == "city"

    # The two groups of points, and the label each one carries.
    assert by_name["Singapore"]["centroid_source"] == "seed-approximate"
    assert by_name["Los Angeles"]["centroid_source"] == "wnut2016-gold-city-centre"

    seeds = [p for p in out["places"] if p["centroid_source"] == "seed-approximate"]
    wnut = [p for p in out["places"] if p["centroid_source"] == "wnut2016-gold-city-centre"]
    assert len(seeds) == 22
    assert len(wnut) == 28

    assert "haversine" in out["distance_method"]
    assert "6371.0088" in out["distance_method"]


def test_an_onboarded_place_says_nothing_it_does_not_know(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as client:
        client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
        out = client.get("/catalogue").json()
    entry = next(p for p in out["places"] if p["name"] == "Sintang")
    assert entry["source"] == "onboarded"
    assert entry["feature_type"] == "onboarded-unverified"
    assert entry["centroid_source"] is None
