"""The per-address budgets, and what each of them covers.

Saving and deleting a profile draw on their own budget, so an operator who
has spent the inference budget can still fix a live draft.
"""

from __future__ import annotations

import io

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app


def _app(monkeypatch, tmp_path, **env: str):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return create_app()


def test_the_limits_are_off_for_a_local_run(monkeypatch, tmp_path) -> None:
    for key in ("MAX_QUERIES_PER_HOUR", "MAX_BATCHES_PER_HOUR", "MAX_PROFILE_SAVES_PER_HOUR"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        for _ in range(35):
            assert client.post("/geolocate", json={"post": "a post"}).status_code == 200
        limits = client.get("/instance").json()["limits"]
    assert limits["queries"]["per_hour"] == 0
    assert limits["batches"]["per_hour"] == 0
    assert limits["profiles"]["per_hour"] == 0


def test_a_correction_survives_a_spent_inference_budget(monkeypatch, tmp_path) -> None:
    app = _app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="1", MAX_PROFILE_SAVES_PER_HOUR="10")
    with TestClient(app) as client:
        assert client.post(
            "/onboard", json={"city": "Sintang", "region": "Indonesia"}
        ).status_code == 200
        assert client.post("/geolocate", json={"post": "a post"}).status_code == 429

        fix = client.put("/onboard", json={"name": "Sintang", "lat": -0.0833, "lon": 111.5})
        assert fix.status_code == 200
        removal = client.request("DELETE", "/onboard", json={"city": "Sintang"})
        assert removal.status_code == 200


def test_the_profile_budget_is_its_own(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_PROFILE_SAVES_PER_HOUR="2")) as client:
        # Drafting is the create step and draws on the inference budget; the
        # three saves below are what the profile budget counts.
        client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
        assert client.put("/onboard", json={"name": "Sintang"}).status_code == 200
        assert client.put("/onboard", json={"name": "Sintang"}).status_code == 200
        refused = client.put("/onboard", json={"name": "Sintang"})
        assert refused.status_code == 429
        # An inference call is unaffected by a spent profile budget.
        assert client.post("/geolocate", json={"post": "a post"}).status_code == 200


def test_a_refused_upload_does_not_spend_a_bulk_slot(monkeypatch, tmp_path) -> None:
    app = _app(monkeypatch, tmp_path, MAX_BATCHES_PER_HOUR="1", MAX_BATCH_BYTES="40")
    with TestClient(app) as client:
        too_big = "id,post\n" + "\n".join(f"{i},a post about Bedok" for i in range(20))
        rejected = client.post(
            "/batch_predict_csv",
            files={"file": ("rows.csv", io.BytesIO(too_big.encode()), "text/csv")},
        )
        assert rejected.status_code == 413

        small = "id,post\n1,Queue at Bedok\n"
        accepted = client.post(
            "/batch_predict_csv",
            files={"file": ("rows.csv", io.BytesIO(small.encode()), "text/csv")},
        )
        assert accepted.status_code == 200


def test_a_refused_row_set_does_not_spend_a_bulk_slot(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_BATCHES_PER_HOUR="1")) as client:
        bad = client.post("/eval", json={
            "inputs": [{"id": "1", "user_handle": "@x", "ground_truth_city": "Bedok"}]
        })
        assert bad.status_code == 422
        good = client.post("/eval", json={
            "inputs": [{"id": "1", "post": "Queue at Bedok", "ground_truth_city": "Bedok"}]
        })
        assert good.status_code == 200


def test_a_429_says_how_long_to_wait(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="1")) as client:
        client.post("/geolocate", json={"post": "a post"})
        refused = client.post("/geolocate", json={"post": "a post"})
    assert refused.status_code == 429
    assert "minutes" in refused.json()["detail"]
    assert int(refused.headers["Retry-After"]) > 0


def test_every_response_says_how_much_budget_is_left(monkeypatch, tmp_path) -> None:
    with TestClient(_app(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="30")) as client:
        resp = client.post("/geolocate", json={"post": "a post"})
    assert resp.headers["X-RateLimit-Limit-Queries"] == "30"
    assert resp.headers["X-RateLimit-Remaining-Queries"] == "29"


@pytest.mark.parametrize("path", ["/instance"])
def test_the_instance_endpoint_reports_the_deployments_settings(
    monkeypatch, tmp_path, path
) -> None:
    app = _app(monkeypatch, tmp_path, GEOLENS_REQUIRE_REGION="1", MAX_BATCHES_PER_HOUR="5")
    with TestClient(app) as client:
        body = client.get(path).json()
    assert body["require_region"] is True
    assert body["limits"]["batches"]["per_hour"] == 5
    assert body["max_batch_rows"] == 50
