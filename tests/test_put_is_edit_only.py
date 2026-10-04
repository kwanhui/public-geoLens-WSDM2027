"""PUT /onboard edits a profile and does not create a place.

A name the catalogue does not hold is a 404; creating a place goes through
POST /onboard. The region check runs on every save against the hint stored
with the profile.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


# ----- edit-only --------------------------------------------------------------

def test_saving_an_unknown_place_is_a_404(client) -> None:
    before = client.get("/catalogue").json()["size"]
    resp = client.put("/onboard", json={"name": "Nowhere At All", "lat": 1.0, "lon": 1.0})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "place_not_in_catalogue"
    assert "POST /onboard" in resp.json()["detail"]
    assert client.get("/catalogue").json()["size"] == before


def test_a_draft_then_a_save_is_the_way_in(client) -> None:
    drafted = client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    assert drafted.status_code == 200
    saved = client.put("/onboard", json={
        "name": "Sintang", "aliases": ["Kota Sintang"], "lat": -0.0833, "lon": 111.5,
    })
    assert saved.status_code == 200
    assert saved.json()["aliases"] == ["Kota Sintang"]


def test_a_built_in_place_can_still_be_overlaid(client) -> None:
    """A built-in place is in the catalogue, so editing it is an edit."""
    resp = client.put("/onboard", json={"name": "Tokyo", "aliases": ["Tokyo-to"]})
    assert resp.status_code == 200
    assert resp.json()["catalogue_status"] == "already_present"


def test_a_save_cannot_grow_the_catalogue(client) -> None:
    before = client.get("/catalogue").json()["size"]
    client.put("/onboard", json={"name": "Tokyo", "aliases": ["Tokyo-to"]})
    client.put("/onboard", json={"name": "Atlantis"})
    assert client.get("/catalogue").json()["size"] == before


def test_a_save_cannot_bypass_a_required_region(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("GEOLENS_REQUIRE_REGION", "1")
    with TestClient(create_app()) as client:
        assert client.post("/onboard", json={"city": "Sintang"}).status_code == 422
        # A save is not a way round that refusal.
        assert client.put("/onboard", json={"name": "Sintang"}).status_code == 404


# ----- the region check runs on every save ------------------------------------

def test_a_swapped_coordinate_warns_on_a_save_that_carries_no_region(client) -> None:
    client.post("/onboard", json={"city": "Milton Keynes", "region": "United Kingdom"})
    # 52.04 N, 0.76 W written the wrong way round.
    body = client.put("/onboard", json={
        "name": "Milton Keynes", "lat": -0.7594, "lon": 52.0406,
    }).json()

    assert any("outside United Kingdom" in w for w in body["warnings"])
    # And the misleading one is gone: the hint is on the profile.
    assert not any("no country or region hint" in w for w in body["warnings"])
    assert body["region"] == "United Kingdom"


def test_a_save_can_still_change_the_region(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    body = client.put("/onboard", json={
        "name": "Sintang", "region": "Malaysia", "lat": -0.0833, "lon": 111.5,
    }).json()
    assert body["region"] == "Malaysia"
    assert any("outside Malaysia" in w for w in body["warnings"])


def test_a_correct_coordinate_saved_without_a_region_does_not_warn(client) -> None:
    client.post("/onboard", json={"city": "Milton Keynes", "region": "United Kingdom"})
    body = client.put("/onboard", json={
        "name": "Milton Keynes", "lat": 52.0406, "lon": -0.7594,
    }).json()
    assert not any("outside" in w for w in body["warnings"])
    assert not any("no country or region hint" in w for w in body["warnings"])


def test_the_saved_region_survives_a_second_save(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    client.put("/onboard", json={"name": "Sintang", "lat": -0.0833, "lon": 111.5})
    body = client.put("/onboard", json={"name": "Sintang", "aliases": ["Kota Sintang"]}).json()
    assert body["region"] == "Indonesia"
