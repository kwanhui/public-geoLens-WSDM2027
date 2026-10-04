from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.ui.server import create_app

SCENARIOS = Path(__file__).resolve().parents[1] / "src/geolens/ui/static/scenarios"


@pytest.fixture
def client(monkeypatch, tmp_path):
    # An isolated cache and no OpenAI key, so the wizard uses its offline
    # template and nothing reaches the network.
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_onboarding_a_new_place_reports_that_it_was_added(client) -> None:
    body = client.post("/onboard", json={"city": "Bidadari Estate"}).json()
    assert body["catalogue_status"] == "added"
    assert body["catalogue_size"] == len(DEFAULT_CITIES) + 1
    assert "new to the catalogue" in body["catalogue_note"]


def test_onboarding_an_existing_city_says_only_the_profile_changed(client) -> None:
    body = client.post("/onboard", json={"city": "Pekanbaru"}).json()
    assert body["catalogue_status"] == "already_present"
    assert body["catalogue_size"] == len(DEFAULT_CITIES)
    assert "only its profile" in body["catalogue_note"]


def test_saving_an_edited_profile_reports_the_catalogue_state(client) -> None:
    client.post("/onboard", json={"city": "Sintang"})
    body = client.put("/onboard", json={
        "name": "Sintang", "aliases": ["Sintang"], "lat": -0.07, "lon": 111.58,
    }).json()
    assert body["catalogue_status"] == "already_present"
    assert body["source"] == "edited"


def test_the_region_hint_is_recorded_and_survives_an_edit(client) -> None:
    drafted = client.post(
        "/onboard", json={"city": "Bidadari Estate", "region": "Singapore"}
    ).json()
    assert drafted["region"] == "Singapore"

    saved = client.put("/onboard", json={
        "name": "Bidadari Estate", "region": "Singapore", "aliases": ["Bidadari"],
        "landmarks": ["Bidadari Park"], "lat": 1.3396, "lon": 103.8720,
    }).json()
    assert saved["region"] == "Singapore"
    assert saved["warnings"] == []


def test_a_coordinate_outside_the_hinted_country_comes_back_as_a_warning(client) -> None:
    client.post("/onboard", json={"city": "Bidadari Estate", "region": "Singapore"})
    body = client.put("/onboard", json={
        "name": "Bidadari Estate", "region": "Singapore", "aliases": ["Bidadari"],
        "landmarks": ["Bidadari Park"], "lat": -6.3005, "lon": 106.8467,
    }).json()
    assert any("outside Singapore" in w for w in body["warnings"])


def test_scenario_region_hints_name_a_country_the_check_knows(client) -> None:
    """A hint the table cannot resolve would silently disable the check."""
    from geolens.onboarding import resolve_country

    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = json.loads(path.read_text())
        if not scenario.get("onboard_city"):
            continue
        hint = scenario.get("region", "")
        assert hint, f"{path.name} onboards a place with no region hint"
        assert resolve_country(hint) is not None, f"{path.name}: unknown region {hint!r}"


def test_reset_puts_the_catalogue_back(client) -> None:
    added = client.post("/onboard", json={"city": "Bidadari Estate"}).json()
    assert added["catalogue_size"] == len(DEFAULT_CITIES) + 1

    body = client.request("DELETE", "/onboard", json={"city": "Bidadari Estate"}).json()
    assert body["catalogue_status"] == "removed"
    assert body["profile_removed"] is True
    assert body["catalogue_size"] == len(DEFAULT_CITIES)

    # And the cold start can be shown again.
    again = client.post("/onboard", json={"city": "Bidadari Estate"}).json()
    assert again["catalogue_status"] == "added"


def test_reset_of_a_built_in_city_clears_its_overlay_and_keeps_the_city(client) -> None:
    client.put("/onboard", json={"name": "Singapore", "aliases": ["Sing city"]})
    body = client.request("DELETE", "/onboard", json={"city": "Singapore"}).json()

    assert body["catalogue_status"] == "built_in_restored"
    assert body["profile_removed"] is True
    assert body["catalogue_size"] == len(DEFAULT_CITIES)


def test_reset_of_an_unknown_place_is_harmless(client) -> None:
    body = client.request("DELETE", "/onboard", json={"city": "Nowhere At All"}).json()
    assert body["catalogue_status"] == "not_present"
    assert body["catalogue_size"] == len(DEFAULT_CITIES)


def test_scenario_places_survive_a_reset_cycle(client) -> None:
    """What the scenario tiles do on every load: reset, then onboard."""
    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = json.loads(path.read_text())
        city = scenario.get("onboard_city")
        if not city:
            continue
        assert client.request("DELETE", "/onboard", json={"city": city}).status_code == 200
        added = client.post("/onboard", json={"city": city}).json()
        assert added["catalogue_status"] == "added", f"{city} is already a catalogue city"
