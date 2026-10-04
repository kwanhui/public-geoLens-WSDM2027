"""Counting how many rows named a catalogue place at all."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.batch import BatchInput, run_batch
from geolens.batch.metrics import compute_rollup, gazetteer_abstentions, named_a_place
from geolens.engines.registry import build_engines
from geolens.ui.server import create_app

NAMES_A_PLACE = "Queue at the Bedok hawker centre again"
NAMES_NOTHING = "The lift is broken again and nobody has come to fix it"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def _rows():
    engines, catalogue = build_engines()
    inputs = [
        BatchInput(id="named", post=NAMES_A_PLACE),
        BatchInput(id="placeless", post=NAMES_NOTHING),
    ]
    return run_batch(inputs, engines, catalogue=catalogue, k=3)


def test_a_row_knows_whether_its_text_named_a_place() -> None:
    rows = {r.id: r for r in _rows()}
    assert named_a_place(rows["named"], "post") is True
    assert named_a_place(rows["placeless"], "post") is False
    # The user-level gazetteer never ran: that is not the same as finding nothing.
    assert named_a_place(rows["named"], "user") is None


def test_the_distribution_splits_named_from_placeless() -> None:
    rollup = {c.city: c for c in compute_rollup(_rows())}
    assert sum(c.post_named + c.post_unnamed for c in rollup.values()) == 2
    assert sum(c.post_unnamed for c in rollup.values()) == 1
    assert rollup["Bedok"].post_named == 1


def test_the_manifest_carries_the_abstention_totals(client) -> None:
    body = client.post("/batch_predict", json={
        "inputs": [
            {"id": "named", "post": NAMES_A_PLACE},
            {"id": "placeless", "post": NAMES_NOTHING},
        ],
    }).json()
    assert body["manifest"]["n_gazetteer_abstained"]["post"] == 1
    assert body["manifest"]["n_gazetteer_abstained"]["user"] == 0
    assert body["manifest"]["n_gazetteer_matched"]["post"] == 1
    counts = {c["city"]: c for c in body["rollup"]}
    assert sum(c["post_unnamed"] for c in counts.values()) == 1


def test_the_totals_match_the_rows() -> None:
    rows = _rows()
    assert gazetteer_abstentions(rows) == {"post": 1, "user": 0}
