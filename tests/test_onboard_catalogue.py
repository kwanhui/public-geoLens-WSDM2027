from __future__ import annotations

import pytest

from geolens.engines._cities import DEFAULT_CITIES
from geolens.onboarding import CityProfile, save_profile
from geolens.onboarding.catalogue import (
    ADDED,
    ALREADY_PRESENT,
    BUILT_IN_RESTORED,
    NOT_PRESENT,
    REMOVED,
    OnboardingRegistry,
    is_default_city,
)
from geolens.onboarding.wizard import _cache_path

HOUR = 3600.0


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))


@pytest.fixture
def registry() -> OnboardingRegistry:
    return OnboardingRegistry(list(DEFAULT_CITIES))


def test_a_new_place_is_added(registry) -> None:
    before = len(registry.catalogue)
    change = registry.register("Bidadari Estate")
    assert change.status == ADDED
    assert change.catalogue_size == before + 1
    assert registry.catalogue[-1] == "Bidadari Estate"


def test_a_place_already_in_the_catalogue_is_reported_as_such(registry) -> None:
    before = len(registry.catalogue)
    change = registry.register("Pekanbaru")
    assert change.status == ALREADY_PRESENT
    assert change.catalogue_size == before
    assert registry.catalogue.count("Pekanbaru") == 1
    assert "already a catalogue city" in change.note


def test_registering_twice_does_not_duplicate(registry) -> None:
    registry.register("Sintang")
    change = registry.register("sintang")
    assert change.status == ALREADY_PRESENT
    assert registry.catalogue.count("Sintang") == 1


def test_reset_removes_an_onboarded_place_and_its_profile(registry) -> None:
    save_profile(CityProfile(name="Sintang", aliases=["Sintang"], lat=-0.07, lon=111.58))
    registry.register("Sintang")
    assert _cache_path("Sintang").exists()

    change = registry.reset("Sintang")
    assert change.status == REMOVED
    assert change.profile_removed is True
    assert "Sintang" not in registry.catalogue
    assert not _cache_path("Sintang").exists()
    assert change.catalogue_size == len(DEFAULT_CITIES)


def test_a_built_in_city_keeps_its_place_and_loses_its_overlay(registry) -> None:
    """Resetting a built-in city drops its edited profile and keeps the city."""
    save_profile(CityProfile(name="Tokyo", aliases=["Tokyo"], lat=35.68, lon=139.65))
    registry.register("Tokyo")
    assert _cache_path("Tokyo").exists()

    change = registry.reset("Tokyo")
    assert change.status == BUILT_IN_RESTORED
    assert change.profile_removed is True
    assert "Tokyo" in registry.catalogue
    assert len(registry.catalogue) == len(DEFAULT_CITIES)
    assert not _cache_path("Tokyo").exists()


def test_a_built_in_city_is_never_dropped_from_the_catalogue(registry) -> None:
    for city in DEFAULT_CITIES:
        assert registry.reset(city).status == BUILT_IN_RESTORED
    assert registry.catalogue == DEFAULT_CITIES


def test_reset_of_a_place_that_was_never_onboarded_changes_nothing(registry) -> None:
    change = registry.reset("Nowhere At All")
    assert change.status == NOT_PRESENT
    assert change.profile_removed is False
    assert len(registry.catalogue) == len(DEFAULT_CITIES)


def test_an_onboarded_place_expires(registry, monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_ONBOARD_TTL_MINUTES", "60")
    now = 1_000_000.0
    registry.register("Sintang", now=now)
    assert "Sintang" in registry.catalogue

    assert registry.expire(now + 59 * 60) == []
    assert "Sintang" in registry.catalogue

    assert registry.expire(now + 61 * 60) == ["Sintang"]
    assert "Sintang" not in registry.catalogue
    assert not _cache_path("Sintang").exists()


def test_a_zero_time_to_live_disables_expiry(registry, monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_ONBOARD_TTL_MINUTES", "0")
    registry.register("Sintang", now=0.0)
    assert registry.expire(10 * HOUR) == []
    assert "Sintang" in registry.catalogue


def test_the_oldest_onboarded_place_is_evicted_at_the_cap(registry, monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_MAX_ONBOARDED", "2")
    registry.register("Sintang", now=1.0)
    registry.register("Bidadari Estate", now=2.0)
    change = registry.register("Kota Belud", now=3.0)

    assert change.evicted == ["Sintang"]
    assert "Sintang" not in registry.catalogue
    assert registry.names() == ["Bidadari Estate", "Kota Belud"]
    assert len(registry.catalogue) == len(DEFAULT_CITIES) + 2


def test_the_status_line_reports_the_cap_and_the_time_left(registry, monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_ONBOARD_TTL_MINUTES", "60")
    monkeypatch.setenv("GEOLENS_MAX_ONBOARDED", "5")
    now = 1_000_000.0
    registry.register("Sintang", now=now)
    status = registry.status(now + 30 * 60)

    assert status["onboarded_count"] == 1
    assert status["onboarded_cap"] == 5
    assert status["onboarding_ttl_minutes"] == 60
    assert status["onboarded"][0]["name"] == "Sintang"
    assert status["onboarded"][0]["expires_in_minutes"] == pytest.approx(30.0, abs=0.5)


def test_the_two_scenario_places_are_not_in_the_catalogue() -> None:
    # The scenarios only demonstrate a cold start while this holds.
    assert not is_default_city("Bidadari Estate")
    assert not is_default_city("Sintang")
