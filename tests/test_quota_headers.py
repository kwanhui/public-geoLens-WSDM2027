"""The rate-limit headers, including what they say when a budget is unlimited."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app

KINDS = ("Queries", "Batches", "Profiles")


def _app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    for key in ("MAX_QUERIES_PER_HOUR", "MAX_BATCHES_PER_HOUR", "MAX_PROFILE_SAVES_PER_HOUR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return create_app()


def test_an_unlimited_budget_sends_no_header(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        resp = client.post("/geolocate", json={"post": "a post"})
    assert resp.status_code == 200
    for kind in KINDS:
        assert f"X-RateLimit-Limit-{kind}" not in resp.headers
        assert f"X-RateLimit-Remaining-{kind}" not in resp.headers


def test_only_the_budgets_the_instance_applies_are_reported(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="30")) as client:
        resp = client.post("/geolocate", json={"post": "a post"})
    assert resp.headers["X-RateLimit-Limit-Queries"] == "30"
    assert resp.headers["X-RateLimit-Remaining-Queries"] == "29"
    assert "X-RateLimit-Remaining-Batches" not in resp.headers
    assert "X-RateLimit-Remaining-Profiles" not in resp.headers


def test_the_remaining_count_falls_with_each_call(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="3")) as client:
        seen = [
            client.post("/geolocate", json={"post": "a post"}).headers[
                "X-RateLimit-Remaining-Queries"
            ]
            for _ in range(3)
        ]
    assert seen == ["2", "1", "0"]


def test_the_headers_are_on_the_429_itself(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="1")) as client:
        client.post("/geolocate", json={"post": "a post"})
        refused = client.post("/geolocate", json={"post": "a post"})
    assert refused.status_code == 429
    assert refused.headers["X-RateLimit-Limit-Queries"] == "1"
    assert refused.headers["X-RateLimit-Remaining-Queries"] == "0"


def test_retry_after_is_the_wait_for_the_oldest_call_to_age_out(monkeypatch, tmp_path) -> None:
    """Not the length of the window: a slot frees when the oldest call does."""
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="1")) as client:
        client.post("/geolocate", json={"post": "a post"})
        refused = client.post("/geolocate", json={"post": "a post"})
    retry_after = int(refused.headers["Retry-After"])
    assert 0 < retry_after <= 3601
    # The one call in the window was made a moment ago, so the wait is
    # within a few seconds of the full hour, and never longer than it.
    assert 3595 <= retry_after <= 3601


def test_the_instance_endpoint_says_a_budget_is_unlimited(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="30")) as client:
        limits = client.get("/instance").json()["limits"]
    assert limits["queries"] == {"per_hour": 30, "remaining": 30}
    # Null, not zero: nothing is left to spend only when there is a budget.
    assert limits["batches"] == {"per_hour": 0, "remaining": None}


@pytest.mark.parametrize("path", ["/catalogue", "/onboard/status", "/healthz"])
def test_a_read_only_endpoint_does_not_claim_an_exhausted_budget(
    monkeypatch, tmp_path, path
) -> None:
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        resp = client.get(path)
    assert resp.status_code == 200
    assert not [h for h in resp.headers if h.lower().startswith("x-ratelimit")]
