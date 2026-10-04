"""What an uploaded CSV may be, and what happens to a row that is not."""

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


def _upload(client, body: bytes, path: str = "/batch_predict_csv", **data):
    return client.post(
        path,
        files={"file": ("rows.csv", io.BytesIO(body), "text/csv")},
        data=data or None,
    )


# ----- the byte-order mark ----------------------------------------------------

def test_a_file_with_a_byte_order_mark_keeps_its_ids(client) -> None:
    body = "﻿id,post\nrow-a,Queue at Bedok\nrow-b,Fire in Tampines\n".encode()
    resp = _upload(client, body)
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["rows"]] == ["row-a", "row-b"]


def test_a_file_without_the_mark_still_works(client) -> None:
    resp = _upload(client, b"id,post\nrow-a,Queue at Bedok\n")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["rows"]] == ["row-a"]


# ----- the id column ----------------------------------------------------------

def test_a_file_with_no_id_column_is_refused_and_names_the_header(client) -> None:
    resp = _upload(client, b"post,bucket\nQueue at Bedok,sg\n")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "missing_id_header"
    assert "`id`" in resp.json()["detail"]
    # The columns the file does have, so a misspelt header is obvious.
    assert "post" in resp.json()["detail"]


def test_a_misspelt_id_column_is_refused_rather_than_numbered(client) -> None:
    resp = _upload(client, b"ID_,post\n7,Queue at Bedok\n")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "missing_id_header"


def test_an_empty_id_cell_is_refused(client) -> None:
    resp = _upload(client, b"id,post\n,Queue at Bedok\n")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "empty_row_id"


def test_duplicate_ids_within_one_batch_are_refused(client) -> None:
    resp = _upload(client, b"id,post\nrow-a,Queue at Bedok\nrow-a,Fire in Tampines\n")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "duplicate_row_ids"
    assert "row-a" in resp.json()["detail"]


def test_duplicate_ids_in_a_json_body_are_refused(client) -> None:
    resp = client.post("/batch_predict", json={"inputs": [
        {"id": "1", "post": "Queue at Bedok"},
        {"id": "1", "post": "Fire in Tampines"},
    ]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "duplicate_row_ids"


# ----- line endings and quoting ----------------------------------------------

def test_crlf_line_endings_parse_as_one_row_each(client) -> None:
    body = b"id,post\r\nrow-a,Queue at Bedok\r\nrow-b,Fire in Tampines\r\n"
    resp = _upload(client, body)
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["rows"]] == ["row-a", "row-b"]


def test_a_quoted_newline_stays_inside_its_cell(client) -> None:
    body = b'id,post\nrow-a,"Queue at Bedok\nagain this morning"\nrow-b,Fire in Tampines\n'
    resp = _upload(client, body)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert [r["id"] for r in rows] == ["row-a", "row-b"]


def test_a_byte_order_mark_and_crlf_together(client) -> None:
    body = "﻿id,post\r\nrow-a,Queue at Bedok\r\n".encode()
    resp = _upload(client, body)
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["rows"]] == ["row-a"]


# ----- ground truth -----------------------------------------------------------

def test_a_truth_with_a_trailing_space_is_still_in_catalogue(client) -> None:
    body = b"id,post,ground_truth_city\nrow-a,Queue at Bedok,Bedok \n"
    resp = _upload(client, body, path="/eval_csv")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["ground_truth_city"] == "Bedok"
    assert row["status"] == "ok"
    assert row["in_catalogue"] is True


def test_a_truth_with_a_trailing_space_in_a_json_body_is_trimmed(client) -> None:
    resp = client.post("/eval", json={
        "inputs": [{"id": "1", "post": "Queue at Bedok", "ground_truth_city": "Bedok "}]
    })
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["status"] == "ok"


def test_an_eval_upload_with_no_truth_column_is_refused(client) -> None:
    resp = _upload(client, b"id,post\nrow-a,Queue at Bedok\n", path="/eval_csv")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "missing_truth_header"
    assert "/batch_predict_csv" in resp.json()["detail"]


def test_an_eval_upload_whose_truth_cells_are_all_empty_is_refused(client) -> None:
    body = b"id,post,ground_truth_city\nrow-a,Queue at Bedok,\n"
    resp = _upload(client, body, path="/eval_csv")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_ground_truth"


def test_an_eval_body_with_no_truth_anywhere_is_refused(client) -> None:
    resp = client.post("/eval", json={"inputs": [{"id": "1", "post": "Queue at Bedok"}]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_ground_truth"


def test_batch_predict_needs_no_truth(client) -> None:
    resp = client.post("/batch_predict", json={"inputs": [{"id": "1", "post": "Queue at Bedok"}]})
    assert resp.status_code == 200
