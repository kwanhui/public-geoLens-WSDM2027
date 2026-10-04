"""Every pin's coordinate comes from GET /catalogue, not from a table in the page."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return create_app()


def test_a_prediction_carries_the_coordinate_of_the_place_it_names(app) -> None:
    with TestClient(app) as client:
        body = client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"}).json()
    named = {e["consensus_city"] for e in body["ensembles"].values()}
    for place in named:
        assert body["place_coordinates"][place] == pytest.approx([1.3521, 103.8198], abs=90)


def test_a_second_visitor_gets_an_onboarded_places_coordinate(app) -> None:
    """Two clients against one instance: the second never onboarded anything."""
    with TestClient(app) as first:
        first.post("/onboard", json={"city": "Sintang", "region": "West Kalimantan, Indonesia"})
        first.put("/onboard", json={
            "name": "Sintang", "region": "West Kalimantan, Indonesia",
            "aliases": ["Kota Sintang"], "lat": -0.0833, "lon": 111.5,
        })
    with TestClient(app) as second:
        catalogue = second.get("/catalogue").json()
        body = second.post("/geolocate", json={"post": "Banjir di Sintang"}).json()

    place = next(p for p in catalogue["places"] if p["name"] == "Sintang")
    assert (place["lat"], place["lon"]) == (-0.0833, 111.5)
    assert body["place_coordinates"]["Sintang"] == [-0.0833, 111.5]


def test_a_batch_response_carries_the_coordinates_of_its_rows(app) -> None:
    with TestClient(app) as client:
        body = client.post("/eval", json={
            "inputs": [{
                "id": "1",
                "post": "Queue at the Bedok hawker centre",
                "ground_truth_city": "Bedok",
            }],
        }).json()
    assert "Bedok" in body["place_coordinates"]
    for coords in body["place_coordinates"].values():
        assert len(coords) == 2


def test_a_place_with_no_coordinate_is_simply_absent(app) -> None:
    with TestClient(app) as client:
        client.put("/onboard", json={"name": "Nowhere Estate", "aliases": ["Nowhere"]})
        body = client.post("/geolocate", json={"post": "Nowhere Estate lift broken"}).json()
    assert "Nowhere Estate" not in body["place_coordinates"]
