"""What a visitor can do to the shared catalogue on a hosted instance."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.onboarding.wizard import _cache_path
from geolens.ui.server import create_app


def _app(monkeypatch, tmp_path, **env: str):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return create_app()


@pytest.fixture
def client(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch, tmp_path)) as c:
        yield c


# ----- built-in profiles ------------------------------------------------------

def test_an_edit_to_a_built_in_profile_can_be_undone(client) -> None:
    client.put("/onboard", json={
        "name": "Tokyo", "aliases": ["Tokyo-to"], "lat": 35.0, "lon": 139.0,
    })
    assert _cache_path("Tokyo").exists()

    body = client.request("DELETE", "/onboard", json={"city": "Tokyo"}).json()
    assert body["catalogue_status"] == "built_in_restored"
    assert body["profile_removed"] is True
    assert not _cache_path("Tokyo").exists()
    assert body["catalogue_size"] == len(DEFAULT_CITIES)


# ----- profile validation -----------------------------------------------------

@pytest.mark.parametrize("alias", ["the", "a", "of", "it", "the city", "was in"])
def test_a_common_word_is_refused_as_an_alias(client, alias) -> None:
    resp = client.put("/onboard", json={"name": "Tokyo", "aliases": [alias]})
    assert resp.status_code == 422
    assert alias in resp.json()["detail"]


def test_a_very_short_alias_is_refused(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    resp = client.put("/onboard", json={"name": "Sintang", "aliases": ["St"]})
    assert resp.status_code == 422
    assert "shorter than" in resp.json()["detail"]


def test_an_out_of_range_centroid_is_refused(client) -> None:
    assert client.put(
        "/onboard", json={"name": "Tokyo", "aliases": ["Tokyo-to"], "lat": 999, "lon": -999}
    ).status_code == 422


@pytest.mark.parametrize("name", ["", "   ", "\t\n", "1234"])
def test_an_empty_or_letterless_place_name_is_refused(client, name) -> None:
    assert client.post("/onboard", json={"city": name}).status_code == 422
    assert client.put("/onboard", json={"name": name}).status_code == 422


def test_a_very_long_place_name_is_refused_rather_than_crashing(client) -> None:
    resp = client.post("/onboard", json={"city": "A" * 5000})
    assert resp.status_code == 422
    assert resp.json()["error"]["field"] == "city"
    assert "80" in resp.json()["detail"]


def test_too_many_list_entries_are_refused(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    resp = client.put(
        "/onboard",
        json={"name": "Sintang", "landmarks": [f"Landmark {i}" for i in range(40)]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["field"] == "landmarks"


def test_a_valid_edit_is_still_accepted(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    body = client.put("/onboard", json={
        "name": "  Sintang  ",
        "region": "Indonesia",
        "aliases": ["Kota Sintang", " Kabupaten Sintang "],
        "landmarks": ["Bukit Kelam"],
        "lat": -0.0833,
        "lon": 111.5,
    }).json()

    assert body["name"] == "Sintang"
    assert body["aliases"] == ["Kota Sintang", "Kabupaten Sintang"]


# ----- expiry and the cap -----------------------------------------------------

def test_the_onboarding_response_reports_the_expiry_and_the_cap(client) -> None:
    body = client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"}).json()

    assert body["onboarding_ttl_minutes"] == 60.0
    assert body["onboarded_cap"] == 20
    assert body["onboarded_count"] == 1
    assert 0 < body["expires_in_minutes"] <= 60.0


def test_the_status_endpoint_lists_what_is_onboarded(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    status = client.get("/onboard/status").json()

    assert status["onboarded_count"] == 1
    assert [p["name"] for p in status["onboarded"]] == ["Sintang"]


def test_the_cap_evicts_the_oldest_onboarded_place(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, GEOLENS_MAX_ONBOARDED="2")) as client:
        client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
        client.post("/onboard", json={"city": "Bidadari Estate", "region": "Singapore"})
        body = client.post("/onboard", json={"city": "Kota Belud", "region": "Malaysia"}).json()

        assert body["evicted"] == ["Sintang"]
        assert body["catalogue_size"] == len(DEFAULT_CITIES) + 2
        names = [p["name"] for p in client.get("/onboard/status").json()["onboarded"]]
        assert names == ["Bidadari Estate", "Kota Belud"]


def test_an_expired_place_leaves_the_catalogue(monkeypatch, tmp_path) -> None:
    with TestClient(
        _app(monkeypatch, tmp_path, GEOLENS_ONBOARD_TTL_MINUTES="0.0001")
    ) as client:
        client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
        import time

        time.sleep(0.02)
        assert client.get("/onboard/status").json()["onboarded_count"] == 0


# ----- the region hint --------------------------------------------------------

def test_a_bare_place_name_warns_about_ambiguity(client) -> None:
    body = client.post("/onboard", json={"city": "Cambridge"}).json()
    assert any("no country or region hint" in w for w in body["warnings"])


def test_a_hinted_place_name_does_not_warn_about_ambiguity(client) -> None:
    body = client.post("/onboard", json={"city": "Cambridge", "region": "United Kingdom"}).json()
    assert not any("no country or region hint" in w for w in body["warnings"])


def test_an_instance_can_require_a_region(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, GEOLENS_REQUIRE_REGION="1")) as client:
        resp = client.post("/onboard", json={"city": "Cambridge"})
        assert resp.status_code == 422
        assert "requires a country or region" in resp.json()["detail"]

        assert client.post(
            "/onboard", json={"city": "Cambridge", "region": "United Kingdom"}
        ).status_code == 200


# ----- the rate limiter -------------------------------------------------------

def test_a_forged_forwarded_header_does_not_reset_the_rate_limit(
    monkeypatch, tmp_path
) -> None:
    """With no trusted proxy the header is client-supplied, so it is ignored."""
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="2")) as client:
        for _ in range(2):
            assert client.post("/geolocate", json={"post": "a post"}).status_code == 200
        assert client.post("/geolocate", json={"post": "a post"}).status_code == 429
        forged = client.post(
            "/geolocate",
            json={"post": "a post"},
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
        assert forged.status_code == 429


def test_behind_one_trusted_proxy_the_hop_it_added_is_used(monkeypatch, tmp_path) -> None:
    """The proxy appends the peer it saw, so the last entry is the real client."""
    with TestClient(
        _app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="1", GEOLENS_TRUSTED_PROXY_HOPS="1")
    ) as client:
        first = client.post(
            "/geolocate",
            json={"post": "a post"},
            headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.9"},
        )
        assert first.status_code == 200

        # Same real client, a different forged prefix: still rate limited.
        again = client.post(
            "/geolocate",
            json={"post": "a post"},
            headers={"X-Forwarded-For": "10.0.0.1, 203.0.113.9"},
        )
        assert again.status_code == 429

        # A genuinely different client behind the same proxy is not.
        other = client.post(
            "/geolocate",
            json={"post": "a post"},
            headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.10"},
        )
        assert other.status_code == 200
