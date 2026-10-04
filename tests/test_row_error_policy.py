"""`on_row_error`, and what one unusable row does to a bulk run.

The default stays `fail`, so nothing that worked before behaves differently.
"""

from __future__ import annotations

import io

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


ROWS = [
    {"id": "good-1", "post": "Queue at the Bedok hawker centre", "ground_truth_city": "Bedok"},
    {"id": "bad-1", "user_handle": "@someone", "ground_truth_city": "Bedok"},
    {"id": "good-2", "post": "Fire in Tampines", "ground_truth_city": "Tampines"},
]


def test_the_default_is_what_it_has_always_been(client) -> None:
    resp = client.post("/eval", json={"inputs": ROWS})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unusable_row"
    assert "bad-1" in resp.json()["detail"]
    # The refusal says how to get the other rows.
    assert "skip" in resp.json()["detail"]


def test_fail_is_the_same_as_leaving_it_out(client) -> None:
    resp = client.post("/eval", json={"inputs": ROWS, "on_row_error": "fail"})
    assert resp.status_code == 422


def test_skip_returns_the_bad_row_and_runs_the_rest(client) -> None:
    body = client.post("/eval", json={"inputs": ROWS, "on_row_error": "skip"}).json()

    rows = {r["id"]: r for r in body["rows"]}
    assert set(rows) == {"good-1", "bad-1", "good-2"}
    assert rows["good-1"]["status"] == "ok"
    assert rows["good-2"]["status"] == "ok"

    bad = rows["bad-1"]
    assert bad["status"] == "error"
    assert "neither a post nor a user timeline" in bad["error"]
    assert bad["per_engine"] == {}

    assert body["summary"]["error_rows"] == 1
    assert body["summary"]["evaluated_rows"] == 2


def test_skip_keeps_the_rows_in_the_order_they_were_sent(client) -> None:
    body = client.post("/eval", json={"inputs": ROWS, "on_row_error": "skip"}).json()
    assert [r["id"] for r in body["rows"]] == ["good-1", "bad-1", "good-2"]


def test_the_csv_form_takes_the_same_policy(client) -> None:
    csv = (
        b"id,post,ground_truth_city\n"
        b"good-1,Queue at Bedok,Bedok\n"
        b"bad-1,,Bedok\n"
        b"good-2,Fire in Tampines,Tampines\n"
    )
    refused = client.post(
        "/eval_csv", files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")}
    )
    assert refused.status_code == 422

    body = client.post(
        "/eval_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"on_row_error": "skip"},
    ).json()
    rows = {r["id"]: r for r in body["rows"]}
    assert rows["bad-1"]["status"] == "error"
    assert rows["good-2"]["status"] == "ok"
    assert body["summary"]["error_rows"] == 1


def test_a_row_whose_cell_is_over_the_cap_is_skipped_too(client) -> None:
    csv = (
        b"id,post\n"
        b"good-1,Queue at Bedok\n"
        b"bad-1," + b"x" * 3000 + b"\n"
    )
    body = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"on_row_error": "skip"},
    ).json()
    rows = {r["id"]: r for r in body["rows"]}
    assert rows["bad-1"]["status"] == "error"
    assert "the limit is" in rows["bad-1"]["error"]
    assert rows["good-1"]["status"] == "ok"


def test_an_empty_id_is_reported_rather_than_refusing_the_file(client) -> None:
    csv = b"id,post\ngood-1,Queue at Bedok\n,Fire in Tampines\n"
    body = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")},
        data={"on_row_error": "skip"},
    ).json()
    statuses = {r["id"]: r["status"] for r in body["rows"]}
    assert statuses["good-1"] == "ok"
    # The row is reported under the line it was on, never under a made-up id.
    assert statuses["(line 3)"] == "error"


def test_an_unknown_policy_is_refused(client) -> None:
    resp = client.post("/eval", json={"inputs": ROWS, "on_row_error": "carry on"})
    assert resp.status_code == 422

    resp = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(b"id,post\n1,a post\n"), "text/csv")},
        data={"on_row_error": "carry on"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["field"] == "on_row_error"


def test_the_size_caps_still_refuse_the_whole_request(monkeypatch, tmp_path) -> None:
    """A cap is about what the instance will allocate, not about one row."""
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("MAX_BATCH_ROWS", "2")
    with TestClient(create_app()) as client:
        resp = client.post("/eval", json={"inputs": ROWS, "on_row_error": "skip"})
    assert resp.status_code == 413
