"""Shape rules for aliases and landmarks, which the gazetteer matches against every post."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.onboarding.validation import (
    MAX_ALIASES,
    MAX_LANDMARKS,
    ProfileError,
    validate_list,
)
from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        c.post("/onboard", json={"city": "Bidadari Estate", "region": "Singapore"})
        yield c


ORDINARY_TEXT = ["the city", "today", "hari ini", "was in", "lagi", "LOL", "in the"]


@pytest.mark.parametrize("value", ORDINARY_TEXT)
def test_an_alias_of_ordinary_text_is_refused(client, value) -> None:
    resp = client.put("/onboard", json={"name": "Bidadari Estate", "aliases": [value]})
    assert resp.status_code == 422, resp.json()


@pytest.mark.parametrize("value", ORDINARY_TEXT)
def test_a_landmark_of_ordinary_text_is_refused(client, value) -> None:
    resp = client.put("/onboard", json={"name": "Bidadari Estate", "landmarks": [value]})
    assert resp.status_code == 422, resp.json()


@pytest.mark.parametrize("value", ["Bidadari", "Bidadari BTO", "Woodleigh MRT", "bidadari"])
def test_a_name_is_accepted_as_an_alias(client, value) -> None:
    resp = client.put("/onboard", json={"name": "Bidadari Estate", "aliases": [value]})
    assert resp.status_code == 200, resp.json()
    assert resp.json()["aliases"] == [value]


def test_an_alias_of_more_than_four_words_is_refused(client) -> None:
    resp = client.put(
        "/onboard",
        json={"name": "Bidadari Estate", "aliases": ["Bidadari Estate Of North East"]},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("field", "named"),
    [("aliases", "An alias"), ("landmarks", "A landmark")],
)
def test_the_word_count_refusal_names_the_field_it_came_from(field, named) -> None:
    """The offending text sits several fields below the message."""
    value = "Yishun Ring Road Central Station"
    with pytest.raises(ProfileError) as excinfo:
        validate_list(field, [value], "Yishun Ring Road")
    message = str(excinfo.value)
    assert message.startswith(named), message
    assert repr(value) in message
    assert "is 5 words; the limit is 4." in message


def test_the_alias_and_landmark_lists_are_capped(client) -> None:
    aliases = [f"Bidadari {i}" for i in range(MAX_ALIASES + 1)]
    assert client.put(
        "/onboard", json={"name": "Bidadari Estate", "aliases": aliases}
    ).status_code == 422
    landmarks = [f"Woodleigh {i}" for i in range(MAX_LANDMARKS + 1)]
    assert client.put(
        "/onboard", json={"name": "Bidadari Estate", "landmarks": landmarks}
    ).status_code == 422


def test_every_token_is_checked_not_only_a_single_word_alias() -> None:
    with pytest.raises(ProfileError):
        validate_list("aliases", ["hari ini"], "Bidadari Estate")
    assert validate_list("aliases", ["Bidadari Park"], "Bidadari Estate") == [
        "Bidadari Park"
    ]


def test_a_stem_of_the_place_name_is_enough(client) -> None:
    """A lower-case alias passes when it shares four letters with the place."""
    resp = client.put(
        "/onboard", json={"name": "Bidadari Estate", "aliases": ["bidadari"]}
    )
    assert resp.status_code == 200
