"""GET /catalogue, and the note the page shows when the engines scatter."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.ui.server import create_app

STATIC = Path(__file__).resolve().parents[1] / "src/geolens/ui/static"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_the_catalogue_lists_every_built_in_place_with_a_coordinate(client) -> None:
    body = client.get("/catalogue").json()

    assert body["size"] == len(DEFAULT_CITIES)
    assert [p["name"] for p in body["places"]] == list(DEFAULT_CITIES)
    assert all(p["source"] == "built-in" for p in body["places"])
    assert all(p["lat"] is not None and p["lon"] is not None for p in body["places"])
    assert all(p["expires_in_minutes"] is None for p in body["places"])


def test_an_onboarded_place_is_marked_and_carries_its_expiry(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    client.put("/onboard", json={
        "name": "Sintang", "region": "Indonesia", "aliases": ["Kota Sintang"],
        "lat": -0.0833, "lon": 111.5,
    })
    body = client.get("/catalogue").json()
    entry = next(p for p in body["places"] if p["name"] == "Sintang")

    assert body["size"] == len(DEFAULT_CITIES) + 1
    assert entry["source"] == "onboarded"
    assert entry["lat"] == pytest.approx(-0.0833)
    assert 0 < entry["expires_in_minutes"] <= body["onboarding_ttl_minutes"]


def test_a_removed_place_leaves_the_listing(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    client.request("DELETE", "/onboard", json={"city": "Sintang"})
    body = client.get("/catalogue").json()

    assert body["size"] == len(DEFAULT_CITIES)
    assert "Sintang" not in [p["name"] for p in body["places"]]


def test_the_agreement_score_is_in_every_single_query_response(client) -> None:
    body = client.post("/geolocate", json={"post": "Queue at the Bedok hawker centre"}).json()
    assert 0.0 <= body["triangulation"]["agreement_score"] <= 1.0


def test_the_interface_lists_the_catalogue_and_warns_on_low_agreement() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'id="catalogue-details"' in html
    assert "Candidate places" in html
    assert 'The catalogue is closed' in html
    assert "async function loadCatalogue(" in js
    assert "The engines disagree:" in js
    assert "const LOW_AGREEMENT" in js
    # The qualification and the closed-catalogue reminder moved behind the
    # note's icon rather than off the page.
    assert "so it abstained" in js
    assert "onboard the place if it is missing" in js
    # What a catalogue row holds, beside the list it describes.
    assert 'id="i-catalogue-rows"' in html
    assert "Each row gives the place, its coordinate and where it came from." in html
