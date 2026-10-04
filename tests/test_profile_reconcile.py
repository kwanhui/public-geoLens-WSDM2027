"""Reconciling the in-memory registry against the profiles on disk.

The two are reconciled at startup and on a timer, so an expiry is a deletion
across a restart.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.onboarding import OnboardingRegistry
from geolens.onboarding.wizard import _cache_path, cached_profile, onboard_city
from geolens.paths import ONBOARDED_CITIES, cache_subdir
from geolens.ui.server import create_app


@pytest.fixture(autouse=True)
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path


def test_an_expired_place_loses_its_profile(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_ONBOARD_TTL_MINUTES", "1")
    onboard_city("Sintang")
    registry = OnboardingRegistry(list(DEFAULT_CITIES))
    registry.register("Sintang", now=0.0)

    registry.expire(now=120.0)
    assert not _cache_path("Sintang").exists()


def test_a_profile_left_by_an_earlier_run_is_discarded() -> None:
    onboard_city("Sintang")
    onboard_city("Tokyo")
    assert _cache_path("Sintang").exists()

    # A fresh registry is what a restart gives: it holds nothing.
    removed = OnboardingRegistry(list(DEFAULT_CITIES)).reconcile()

    assert len(removed) == 2
    assert not _cache_path("Sintang").exists()
    assert not _cache_path("Tokyo").exists()


def test_a_live_place_keeps_its_profile() -> None:
    onboard_city("Sintang")
    registry = OnboardingRegistry(list(DEFAULT_CITIES))
    registry.register("Sintang")
    assert registry.reconcile() == []
    assert cached_profile("Sintang") is not None


def test_a_file_under_an_older_key_is_discarded() -> None:
    directory = cache_subdir(ONBOARDED_CITIES, create=True)
    stale = directory / "sintang.json"
    stale.write_text(json.dumps({"name": "Sintang"}))
    OnboardingRegistry(list(DEFAULT_CITIES)).reconcile()
    assert not stale.exists()


def test_the_server_reconciles_at_startup() -> None:
    onboard_city("Sintang")
    assert _cache_path("Sintang").exists()
    with TestClient(create_app()) as client:
        names = [p["name"] for p in client.get("/catalogue").json()["places"]]
    assert "Sintang" not in names
    assert not _cache_path("Sintang").exists()
