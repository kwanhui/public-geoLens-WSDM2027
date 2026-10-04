"""A place is a neighbourhood or a town, not a building or an address."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.onboarding.validation import address_like_reason
from geolens.ui.server import create_app

ADDRESS_LIKE = [
    "Blk 118 Bidadari",
    "Unit 12 Woodleigh",
    "Marina Bay Residence",
    "Tan House",
    "Lot 5 Sintang",
    "Sintang Apartment",
]


def make_client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app())


@pytest.mark.parametrize("name", ADDRESS_LIKE)
def test_an_address_reads_as_an_address(name) -> None:
    assert address_like_reason(name) is not None


@pytest.mark.parametrize("name", ["Sintang", "Bidadari Estate", "Kuala Lumpur"])
def test_a_place_does_not(name) -> None:
    assert address_like_reason(name) is None


@pytest.mark.parametrize("name", ADDRESS_LIKE)
def test_an_address_is_refused_where_the_deployment_says_so(
    monkeypatch, tmp_path, name
) -> None:
    with make_client(monkeypatch, tmp_path, GEOLENS_REFUSE_ADDRESS_LIKE="1") as client:
        resp = client.post("/onboard", json={"city": name, "region": "Singapore"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "place_too_fine_grained"


def test_an_address_is_warned_about_otherwise(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path) as client:
        body = client.post(
            "/onboard", json={"city": "Blk 118 Bidadari", "region": "Singapore"}
        ).json()
    assert any("address" in w for w in body["warnings"])


def test_an_onboarded_place_is_marked_unverified(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path) as client:
        client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
        entry = next(
            p for p in client.get("/catalogue").json()["places"] if p["name"] == "Sintang"
        )
    assert entry["feature_type"] == "onboarded-unverified"
    assert entry["centroid_source"] is None


def test_a_strict_instance_says_it_refuses(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path, GEOLENS_REFUSE_ADDRESS_LIKE="1") as client:
        assert client.get("/instance").json()["refuse_address_like"] is True


def test_a_lenient_instance_says_it_warns(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path) as client:
        assert client.get("/instance").json()["refuse_address_like"] is False
