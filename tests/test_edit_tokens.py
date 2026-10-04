"""Who may edit or remove a place on a shared instance.

POST /onboard mints an edit token. Where the deployment sets
`GEOLENS_REQUIRE_EDIT_TOKEN`, PUT and DELETE need it; an operator token from
the environment passes everything.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app


def make_client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app())


@pytest.fixture
def guarded(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path, GEOLENS_REQUIRE_EDIT_TOKEN="1") as c:
        yield c


@pytest.fixture
def open_instance(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as c:
        yield c


def draft(client, city="Bidadari Estate"):
    body = client.post("/onboard", json={"city": city, "region": "Singapore"}).json()
    return body["edit_token"]


def test_drafting_returns_a_token(guarded) -> None:
    token = draft(guarded)
    assert isinstance(token, str) and len(token) >= 16


def test_an_edit_without_the_token_is_refused(guarded) -> None:
    draft(guarded)
    resp = guarded.put("/onboard", json={"name": "Bidadari Estate", "aliases": ["Bidadari"]})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "edit_token_required"
    assert resp.json()["error"]["field"] == "edit_token"


def test_a_wrong_token_is_refused(guarded) -> None:
    draft(guarded)
    resp = guarded.put(
        "/onboard",
        json={"name": "Bidadari Estate", "aliases": ["Bidadari"], "edit_token": "nope"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "edit_token_invalid"


def test_the_token_in_the_body_is_accepted(guarded) -> None:
    token = draft(guarded)
    resp = guarded.put(
        "/onboard",
        json={"name": "Bidadari Estate", "aliases": ["Bidadari"], "edit_token": token},
    )
    assert resp.status_code == 200, resp.json()


def test_the_token_in_the_header_is_accepted(guarded) -> None:
    token = draft(guarded)
    resp = guarded.put(
        "/onboard",
        json={"name": "Bidadari Estate", "aliases": ["Bidadari"]},
        headers={"X-GeoLens-Edit-Token": token},
    )
    assert resp.status_code == 200, resp.json()


def test_a_removal_needs_the_token_too(guarded) -> None:
    token = draft(guarded)
    assert guarded.request(
        "DELETE", "/onboard", json={"city": "Bidadari Estate"}
    ).status_code == 403
    assert guarded.request(
        "DELETE", "/onboard", json={"city": "Bidadari Estate", "edit_token": token}
    ).status_code == 200


def test_the_operator_token_passes_everything(monkeypatch, tmp_path) -> None:
    with make_client(
        monkeypatch,
        tmp_path,
        GEOLENS_REQUIRE_EDIT_TOKEN="1",
        GEOLENS_OPERATOR_TOKEN="let-me-in",
    ) as client:
        draft(client)
        resp = client.request(
            "DELETE",
            "/onboard",
            json={"city": "Bidadari Estate"},
            headers={"X-GeoLens-Operator-Token": "let-me-in"},
        )
        assert resp.status_code == 200, resp.json()


def test_a_local_run_needs_no_token(open_instance) -> None:
    draft(open_instance)
    assert open_instance.put(
        "/onboard", json={"name": "Bidadari Estate", "aliases": ["Bidadari"]}
    ).status_code == 200


def test_a_guarded_instance_says_a_token_is_needed(guarded) -> None:
    assert guarded.get("/instance").json()["require_edit_token"] is True


def test_a_local_instance_says_none_is(open_instance) -> None:
    assert open_instance.get("/instance").json()["require_edit_token"] is False


def test_second_caller_gets_no_token_and_cannot_redraft(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_REQUIRE_EDIT_TOKEN", "1")
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    from fastapi.testclient import TestClient

    from geolens.ui.server import create_app

    client = TestClient(create_app())
    first = client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"}).json()
    assert first["edit_token"]
    second = client.post(
        "/onboard", json={"city": "Sintang", "region": "Malaysia", "force_refresh": True}
    ).json()
    assert second["edit_token"] is None
    assert second["region"] == "Indonesia"
    again = client.post(
        "/onboard",
        json={"city": "Sintang", "region": "Indonesia"},
        headers={"X-GeoLens-Edit-Token": first["edit_token"]},
    ).json()
    assert again["edit_token"] == first["edit_token"]


def test_removing_an_absent_place_needs_no_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_REQUIRE_EDIT_TOKEN", "1")
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    client = TestClient(create_app())
    gone = client.request("DELETE", "/onboard", json={"city": "Sintang", "edit_token": "stale"})
    assert gone.status_code == 200
    assert gone.json()["catalogue_status"] == "not_present"
