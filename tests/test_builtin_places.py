"""The 50 built-in places stay in the catalogue whatever onboarding does."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.onboarding import OnboardingRegistry
from geolens.onboarding.catalogue import DEFAULT_MAX_ONBOARDED
from geolens.places import name_key
from geolens.ui.server import create_app


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    return OnboardingRegistry(list(DEFAULT_CITIES))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("GEOLENS_MAX_ONBOARDED", "3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def assert_all_built_in_present(catalogue: list[str]) -> None:
    present = {name_key(c) for c in catalogue}
    missing = [c for c in DEFAULT_CITIES if name_key(c) not in present]
    assert not missing, f"the catalogue lost {missing}"


def test_builtin_places_survive_eviction(registry) -> None:
    for i in range(DEFAULT_MAX_ONBOARDED + 5):
        registry.register("Tokyo")
        registry.register(f"Sintang {i}")
        assert_all_built_in_present(registry.catalogue)
    assert_all_built_in_present(registry.catalogue)


def test_builtin_places_survive_expiry(monkeypatch, registry) -> None:
    monkeypatch.setenv("GEOLENS_ONBOARD_TTL_MINUTES", "1")
    registry.register("Tokyo", now=0.0)
    registry.register("Sintang", now=0.0)
    dropped = registry.expire(now=120.0)

    assert "Sintang" in dropped
    assert "Tokyo" in dropped  # the overlay, not the place
    assert_all_built_in_present(registry.catalogue)


def test_builtin_places_survive_a_reset(registry) -> None:
    registry.register("Tokyo")
    change = registry.reset("Tokyo")
    assert change.status == "built_in_restored"
    assert_all_built_in_present(registry.catalogue)


def test_a_builtin_overlay_takes_no_onboarded_slot(registry) -> None:
    for name in ("Tokyo", "London", "Bedok"):
        registry.register(name)
    assert registry.count == 0
    assert registry.overlay_names() == ["Tokyo", "London", "Bedok"]
    assert_all_built_in_present(registry.catalogue)


def test_saving_a_builtin_profile_repeatedly_keeps_the_place(client) -> None:
    for _ in range(21):
        assert client.put(
            "/onboard", json={"name": "Tokyo", "aliases": ["Tokyo-to"]}
        ).status_code == 200
    names = [p["name"] for p in client.get("/catalogue").json()["places"]]
    assert_all_built_in_present(names)
    assert client.get("/healthz").json()["built_in_places_present"] is True


def test_healthz_reports_the_built_in_places(client) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["built_in_places_present"] is True
    assert body["missing_built_in"] == []
