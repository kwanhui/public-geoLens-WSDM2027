"""`flag_radius_km` as a request parameter.

The range is 1 to 20,015 km and the default is the 161 km every reported
number was produced under. The value used is recorded in the run manifest.
"""

from __future__ import annotations

import io

import pytest
from starlette.testclient import TestClient

from geolens.engines.base import Prediction
from geolens.geo import ACC_KM_THRESHOLD
from geolens.triangulator import triangulate
from geolens.ui.server import create_app

GRANULARITIES = {"post_engine": "post", "user_engine": "user"}

# Singapore to Kuala Lumpur is about 315 km: flagged at the 161 km default,
# not flagged at a 500 km radius.
SG_KL = {
    "post_engine": Prediction(city="Singapore", confidence=0.9, top_k=[("Singapore", 0.9)]),
    "user_engine": Prediction(
        city="Kuala Lumpur", confidence=0.8, top_k=[("Kuala Lumpur", 0.8)]
    ),
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


def test_the_default_radius_is_the_reported_one() -> None:
    assert ACC_KM_THRESHOLD == 161.0
    assert triangulate(SG_KL, engines=GRANULARITIES).disagreement_flag is True


def test_a_wider_radius_suppresses_the_flag() -> None:
    tri = triangulate(SG_KL, engines=GRANULARITIES, radius_km=500.0)

    assert tri.disagreement_flag is False
    assert tri.disagreement_km is not None
    assert "inside the 500 km radius" in tri.notes[0]


def test_a_narrower_radius_raises_it() -> None:
    near = {
        "post_engine": Prediction(city="Bedok", confidence=0.9, top_k=[("Bedok", 0.9)]),
        "user_engine": Prediction(city="Tampines", confidence=0.8, top_k=[("Tampines", 0.8)]),
    }
    assert triangulate(near, engines=GRANULARITIES).disagreement_flag is False
    assert triangulate(near, engines=GRANULARITIES, radius_km=1.0).disagreement_flag is True


def test_geolocate_accepts_the_radius_and_records_it(client) -> None:
    body = client.post(
        "/geolocate",
        json={"post": "a post", "user_posts": ["one", "two"], "flag_radius_km": 4000},
    ).json()

    assert body["manifest"]["flag_radius_km"] == 4000


def test_the_default_is_recorded_when_the_caller_omits_it(client) -> None:
    body = client.post("/geolocate", json={"post": "a post"}).json()
    assert body["manifest"]["flag_radius_km"] == ACC_KM_THRESHOLD


@pytest.mark.parametrize("value", [0, -5, 40000])
def test_an_out_of_range_radius_is_refused(client, value) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "flag_radius_km": value})
    assert resp.status_code == 422


def test_batch_endpoints_accept_the_radius(client) -> None:
    body = client.post(
        "/batch_predict",
        json={
            "inputs": [{"id": "1", "post": "a post", "user_posts": ["x", "y"]}],
            "flag_radius_km": 900,
        },
    ).json()
    assert body["manifest"]["flag_radius_km"] == 900

    resp = client.post(
        "/eval",
        json={
            "inputs": [{"id": "1", "post": "a post", "ground_truth_city": "Bedok"}],
            "flag_radius_km": 40000,
        },
    )
    assert resp.status_code == 422


def test_the_csv_form_accepts_the_radius_and_range_checks_it(client) -> None:
    csv = b"id,post,user_posts\n1,a post,one|two\n"

    ok = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"flag_radius_km": "900"},
    )
    assert ok.status_code == 200
    assert ok.json()["manifest"]["flag_radius_km"] == 900

    bad = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"flag_radius_km": "0"},
    )
    assert bad.status_code == 422
    assert "flag_radius_km" in bad.json()["detail"]
