"""Every request field is bounded, and a breach says which field and why."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from geolens.ui.limits import MAX_POST_CHARS, MAX_TIMELINE_POSTS
from geolens.ui.server import create_app

APP_JS = (Path(__file__).resolve().parents[1] / "src/geolens/ui/static/app.js").read_text()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


def _detail(resp) -> str:
    return str(resp.json()["detail"])


@pytest.mark.parametrize("k", [0, -1, 21, 10_000])
def test_an_out_of_range_k_is_refused(client, k) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "k": k})
    assert resp.status_code == 422
    assert "k" in _detail(resp)


def test_k_at_the_bounds_is_accepted(client) -> None:
    for k in (1, 20):
        assert client.post("/geolocate", json={"post": "a post", "k": k}).status_code == 200


def test_an_unknown_fusion_method_is_refused(client) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "ensemble_method": "bogus"})
    assert resp.status_code == 422
    assert "weighted" in _detail(resp)


def test_an_oversized_post_is_refused(client) -> None:
    resp = client.post("/geolocate", json={"post": "x" * (MAX_POST_CHARS + 1)})
    assert resp.status_code == 422
    assert str(MAX_POST_CHARS) in _detail(resp)


@pytest.mark.parametrize("post", ["", "   ", "\t\n  "])
def test_a_whitespace_only_post_is_refused(client, post) -> None:
    resp = client.post("/geolocate", json={"post": post})
    assert resp.status_code == 422
    assert "Provide a post" in _detail(resp)


def test_a_handle_on_its_own_is_refused(client) -> None:
    """No engine reads the handle, so it gives them nothing to run on."""
    resp = client.post("/geolocate", json={"user_handle": "@someone"})
    assert resp.status_code == 422
    assert "user_handle" in _detail(resp)


def test_an_oversized_timeline_is_refused(client) -> None:
    resp = client.post(
        "/geolocate",
        json={"post": "a post", "user_posts": ["x"] * (MAX_TIMELINE_POSTS + 1)},
    )
    assert resp.status_code == 422
    assert "user_posts" in _detail(resp)


def test_blank_timeline_entries_are_dropped(client) -> None:
    body = client.post(
        "/geolocate", json={"post": "a post", "user_posts": ["  ", "", "\t"]}
    ).json()
    assert set(body["ensembles"]) == {"post"}


def test_batch_rows_are_bounded_too(client) -> None:
    resp = client.post(
        "/batch_predict",
        json={"inputs": [{"id": "1", "post": "x" * (MAX_POST_CHARS + 1)}]},
    )
    assert resp.status_code == 422

    empty = client.post("/batch_predict", json={"inputs": []})
    assert empty.status_code == 422


def test_a_batch_row_with_no_text_names_the_row(client) -> None:
    resp = client.post(
        "/batch_predict", json={"inputs": [{"id": "row-7", "user_handle": "@x"}]}
    )
    assert resp.status_code == 422
    assert "row-7" in _detail(resp)


def test_an_uploaded_csv_is_bounded_and_names_the_row(client) -> None:
    csv = ("id,post\n1,ok post\n2," + "x" * (MAX_POST_CHARS + 1) + "\n").encode()
    resp = client.post(
        "/batch_predict_csv", files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")}
    )
    assert resp.status_code == 422
    assert "'2'" in _detail(resp)


def test_the_csv_form_range_checks_k_and_the_fusion_method(client) -> None:
    csv = b"id,post\n1,a post\n"

    bad_k = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"k": "0"},
    )
    assert bad_k.status_code == 422
    # The bound is declared on the form field, so it is in the OpenAPI
    # document and pydantic refuses before the handler runs.
    assert bad_k.json()["error"]["field"] == "k"

    bad_method = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"ensemble_method": "bogus"},
    )
    assert bad_method.status_code == 422
    assert "ensemble_method" in _detail(bad_method)


def test_the_interface_shows_the_message_and_clears_stale_results() -> None:
    assert "function detailText(" in APP_JS
    assert "function clearResults(" in APP_JS
    assert "if (!keepResults) clearResults();" in APP_JS
    assert "alert(" not in APP_JS, "an alert box hides the message behind a dialog"
