"""One place, one profile file, keyed on the place identifier.

The file name comes from the normalised name rather than a transliteration,
so two places whose names share no ASCII letters keep separate profiles.
"""

from __future__ import annotations

import json

import pytest

from geolens.onboarding.wizard import _cache_path, cached_profile, onboard_city
from geolens.paths import ONBOARDED_CITIES, cache_subdir
from geolens.places import place_id


@pytest.fixture(autouse=True)
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("first", "second"),
    [("東京", "北京"), ("St Louis", "St. Louis"), ("Bedok", "Bedok North")],
)
def test_two_places_keep_two_profiles(first, second) -> None:
    onboard_city(first)
    onboard_city(second)
    assert _cache_path(first) != _cache_path(second)
    assert cached_profile(first).name == first
    assert cached_profile(second).name == second


def test_the_file_is_named_after_the_place_identifier() -> None:
    onboard_city("Bidadari Estate")
    path = _cache_path("Bidadari Estate")
    assert path.name == f"{place_id('Bidadari Estate')}.json"
    assert json.loads(path.read_text())["name"] == "Bidadari Estate"


def test_a_file_written_under_an_older_key_is_not_read() -> None:
    directory = cache_subdir(ONBOARDED_CITIES, create=True)
    (directory / "bidadari-estate.json").write_text(
        json.dumps({"name": "Bidadari Estate", "notes": "stale"})
    )
    assert cached_profile("Bidadari Estate") is None
