"""The manifest as a record of what was run, on what, by which instance."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines.llm_classifier import MAX_TOKENS, TEMPERATURE
from geolens.engines.llm_claude import MAX_TOKENS as CLAUDE_MAX_TOKENS
from geolens.manifest import catalogue_hash, input_fingerprint
from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_the_same_rows_fingerprint_the_same_whatever_the_form() -> None:
    rows = [{"id": "1", "post": "Queue at Bedok", "user_posts": None}]
    assert input_fingerprint(rows) == input_fingerprint(list(rows))
    assert input_fingerprint(rows) != input_fingerprint(
        [{"id": "1", "post": "Queue at Tampines", "user_posts": None}]
    )
    assert len(input_fingerprint(rows)) == 64


def test_a_single_query_records_what_was_submitted(client) -> None:
    m = client.post(
        "/geolocate", json={"post": "Fire at Marina Bay Sands", "user_posts": ["Shibuya"]}
    ).json()["manifest"]

    assert m["input_row_count"] == 1
    assert m["input_sha256"] == input_fingerprint(
        [{"post": "Fire at Marina Bay Sands", "user_posts": ["Shibuya"]}]
    )
    assert m["base_url"].startswith("http")


def test_a_bulk_run_records_the_row_count_and_the_fingerprint(client) -> None:
    m = client.post("/batch_predict", json={
        "inputs": [
            {"id": "1", "post": "Queue at Bedok"},
            {"id": "2", "post": "Ramen in Tokyo"},
        ],
    }).json()["manifest"]

    assert m["input_row_count"] == 2
    assert len(m["input_sha256"]) == 64


def test_the_catalogue_is_listed_not_only_hashed(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    client.put("/onboard", json={
        "name": "Sintang", "region": "Indonesia", "aliases": ["Kota Sintang"],
        "lat": -0.0833, "lon": 111.5,
    })
    m = client.post("/geolocate", json={"post": "Banjir di Sintang"}).json()["manifest"]

    catalogue = m["catalogue"]
    assert catalogue["built_in_sha"] == catalogue_hash(list(DEFAULT_CITIES))
    assert "Singapore" in catalogue["built_in_names"]
    onboarded = {p["name"]: p for p in catalogue["onboarded"]}
    assert onboarded["Sintang"]["lat"] == -0.0833
    assert onboarded["Sintang"]["aliases"] == ["Kota Sintang"]
    assert onboarded["Sintang"]["age_minutes"] >= 0


def test_the_sampling_parameters_are_the_ones_the_adapters_send(client) -> None:
    m = client.post("/geolocate", json={"post": "a post"}).json()["manifest"]
    sampling = m["sampling_parameters"]

    assert sampling["gpt4o_mini_post"]["temperature"] == TEMPERATURE
    assert sampling["gpt4o_mini_post"]["max_tokens"] == MAX_TOKENS
    # The Claude adapter sends no temperature at all, and says so rather than
    # letting the manifest name one.
    assert sampling["claude_haiku_post"]["temperature"] is None
    assert sampling["claude_haiku_post"]["max_tokens"] == CLAUDE_MAX_TOKENS
    assert "no sampling parameter is sent" in sampling["claude_haiku_post"]["note"]
    # An engine that calls no model claims nothing.
    assert "gazetteer_post" not in sampling
