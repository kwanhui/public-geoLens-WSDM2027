"""A drafted coordinate that lands on a place already in the catalogue."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.onboarding.proximity import nearest_catalogue_place, proximity_warning
from geolens.ui.server import create_app

CATALOGUE = ["Singapore", "Tokyo", "Bedok"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_a_centroid_on_an_existing_place_is_warned_about() -> None:
    warning = proximity_warning("Canberra", 1.3521, 103.8198, CATALOGUE)
    assert warning is not None
    assert "Singapore" in warning


def test_a_centroid_a_few_hundred_metres_away_is_still_warned_about() -> None:
    assert proximity_warning("Canberra", 1.3550, 103.8198, CATALOGUE) is not None


def test_a_distinct_place_is_left_alone() -> None:
    assert proximity_warning("Sintang", -0.0833, 111.5, CATALOGUE) is None


def test_a_place_is_not_compared_with_itself() -> None:
    assert proximity_warning("Singapore", 1.3521, 103.8198, CATALOGUE) is None


def test_the_nearest_place_is_reported_with_its_distance() -> None:
    nearest = nearest_catalogue_place(1.3236, 103.9273, CATALOGUE)
    assert nearest is not None
    assert nearest[0] == "Bedok"
    assert nearest[1] < 1.0


def test_the_onboarding_panel_shows_the_warning(client) -> None:
    client.post("/onboard", json={"city": "Canberra", "region": "Singapore"})
    body = client.put("/onboard", json={
        "name": "Canberra", "region": "Singapore", "lat": 1.3521, "lon": 103.8198,
    }).json()
    assert any("already" in w and "Singapore" in w for w in body["warnings"])
