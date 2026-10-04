from __future__ import annotations

import io

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app

LOCAL_ENGINES = [
    "contrastgeo",
    "fewuser",
    "retrievezero",
    "gazetteer_post",
    "gazetteer_user",
]
THIRD_PARTY = [
    "gpt4o_mini_post",
    "gpt4o_mini_user",
    "claude_haiku_post",
    "claude_haiku_user",
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


def test_the_local_engines_can_run_on_their_own(client) -> None:
    body = client.post("/geolocate", json={
        "post": "Fire at Marina Bay Sands in Singapore",
        "user_posts": ["Ramen in Tokyo again"],
        "engines": LOCAL_ENGINES,
    }).json()
    for name in LOCAL_ENGINES:
        assert not body["per_engine"][name]["skipped"], name
    for name in THIRD_PARTY:
        assert body["per_engine"][name]["skipped"]
        assert body["per_engine"][name]["reason"] == "not selected"


def test_the_selection_is_recorded_in_the_manifest(client) -> None:
    body = client.post("/geolocate", json={
        "post": "Fire at Marina Bay Sands", "engines": ["gazetteer_post"],
    }).json()
    assert body["manifest"]["selected_engines"] == ["gazetteer_post"]


def test_every_engine_runs_when_none_is_named(client) -> None:
    body = client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"}).json()
    assert body["manifest"]["selected_engines"] == list(body["per_engine"])
    assert not any(p["reason"] == "not selected" for p in body["per_engine"].values())


def test_an_unknown_engine_name_is_refused(client) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "engines": ["gpt5"]})
    assert resp.status_code == 422
    assert "gpt5" in resp.json()["detail"]


def test_an_empty_engine_list_is_refused(client) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "engines": []})
    assert resp.status_code == 422


def test_the_batch_endpoints_take_the_same_selection(client) -> None:
    body = client.post("/eval", json={
        "inputs": [{"id": "1", "post": "Queue at Bedok", "ground_truth_city": "Bedok"}],
        "engines": ["gazetteer_post"],
    }).json()
    assert body["manifest"]["selected_engines"] == ["gazetteer_post"]
    assert body["rows"][0]["per_engine"]["claude_haiku_post"]["reason"] == "not selected"


def test_the_csv_form_takes_the_same_selection(client) -> None:
    csv = "id,post\n1,Queue at the Bedok hawker centre\n"
    resp = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv.encode()), "text/csv")},
        data={"engines": "gazetteer_post,contrastgeo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["manifest"]["selected_engines"] == ["gazetteer_post", "contrastgeo"]
    assert body["rows"][0]["per_engine"]["gpt4o_mini_post"]["reason"] == "not selected"


def test_the_registry_says_which_engines_call_a_third_party(client) -> None:
    body = client.get("/instance").json()
    third_party = [n for n, e in body["engines"].items() if e["calls_a_third_party"]]
    assert sorted(third_party) == sorted(THIRD_PARTY)


def test_the_local_preset_is_the_gazetteer_and_the_encoders(monkeypatch, tmp_path) -> None:
    """What the server calls local: everything that sends no text off it."""
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        body = client.get("/instance").json()
    assert set(body["local_engines"]) == set(LOCAL_ENGINES)
    for name in THIRD_PARTY:
        assert name in body["engines"]
        assert name not in body["local_engines"]
