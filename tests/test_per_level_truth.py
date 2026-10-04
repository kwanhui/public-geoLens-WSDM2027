from __future__ import annotations

import io

import pytest
from starlette.testclient import TestClient

from geolens.batch import BatchInput, compute_summary, run_batch
from geolens.batch.metrics import truth_for
from geolens.batch.runner import BatchRow
from geolens.engines.registry import build_engines
from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


def _row(**kwargs) -> BatchRow:
    return BatchRow(id="1", status="ok", in_catalogue=True, **kwargs)


def test_the_user_level_truth_is_used_only_for_user_level_engines() -> None:
    row = _row(ground_truth_city="Singapore", ground_truth_user_city="Tokyo")

    assert truth_for(row, "post") == "Singapore"
    assert truth_for(row, "user") == "Tokyo"
    assert truth_for(row, None) == "Singapore"


def test_without_the_column_every_level_uses_the_one_truth() -> None:
    row = _row(ground_truth_city="Singapore")

    assert truth_for(row, "post") == "Singapore"
    assert truth_for(row, "user") == "Singapore"


def _score(rows: list[BatchInput]):
    engines, catalogue = build_engines()
    granularities = {n: e.granularity for n, e in engines.items()}
    results = run_batch(rows, engines, catalogue=catalogue, k=5)
    return compute_summary(
        results, catalogue_size=len(catalogue), granularities=granularities
    )


def test_a_disagreeing_row_is_scored_correctly_at_both_levels(client) -> None:
    """The gazetteer reads the text, so each level has a checkable answer."""
    rows = [
        BatchInput(
            id="disagree-1",
            post="Massive fire at Marina Bay Sands in Singapore",
            user_posts=["Ramen in Tokyo again", "Tokyo is cold tonight"],
            ground_truth_city="Singapore",
            ground_truth_user_city="Tokyo",
        )
    ]
    summary = _score(rows)

    assert summary.per_engine["gazetteer_post"].acc_at_1 == 1.0
    assert summary.per_engine["gazetteer_user"].acc_at_1 == 1.0
    assert summary.ensembles["post"].acc_at_1 == 1.0
    assert summary.ensembles["user"].acc_at_1 == 1.0


def test_the_same_row_scores_the_user_level_wrong_without_the_column(client) -> None:
    rows = [
        BatchInput(
            id="disagree-1",
            post="Massive fire at Marina Bay Sands in Singapore",
            user_posts=["Ramen in Tokyo again", "Tokyo is cold tonight"],
            ground_truth_city="Singapore",
        )
    ]
    summary = _score(rows)

    assert summary.per_engine["gazetteer_post"].acc_at_1 == 1.0
    assert summary.per_engine["gazetteer_user"].acc_at_1 == 0.0


def test_the_eval_endpoint_takes_the_column(client) -> None:
    body = client.post(
        "/eval",
        json={
            "inputs": [{
                "id": "1",
                "post": "Marina Bay Sands in Singapore",
                "user_posts": ["Ramen in Tokyo", "Tokyo again"],
                "ground_truth_city": "Singapore",
                "ground_truth_user_city": "Tokyo",
            }],
        },
    ).json()

    assert body["rows"][0]["ground_truth_user_city"] == "Tokyo"
    assert body["summary"]["per_engine"]["gazetteer_user"]["acc_at_1"] == 1.0


def test_the_csv_form_takes_the_column(client) -> None:
    csv = (
        b"id,post,user_posts,ground_truth_city,ground_truth_user_city\n"
        b"1,Marina Bay Sands in Singapore,Ramen in Tokyo|Tokyo again,Singapore,Tokyo\n"
    )
    body = client.post(
        "/eval_csv", files={"file": ("rows.csv", io.BytesIO(csv), "text/csv")}
    ).json()

    assert body["rows"][0]["ground_truth_user_city"] == "Tokyo"
    assert body["summary"]["per_engine"]["gazetteer_user"]["acc_at_1"] == 1.0


def test_a_user_truth_outside_the_catalogue_marks_the_row_out_of_catalogue(client) -> None:
    body = client.post(
        "/eval",
        json={
            "inputs": [{
                "id": "1",
                "post": "a post",
                "user_posts": ["one", "two"],
                "ground_truth_city": "Singapore",
                "ground_truth_user_city": "Ulaanbaatar",
            }],
        },
    ).json()

    assert body["rows"][0]["status"] == "ooc"
    assert body["summary"]["ooc_rows"] == 1
