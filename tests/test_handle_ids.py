from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def run(client, ids):
    return client.post(
        "/batch_predict",
        json={"inputs": [{"id": i, "post": "Fire at Marina Bay Sands"} for i in ids]},
    ).json()


@pytest.mark.parametrize("row_id", ["@alice", "@a_b_c", "alice_wong", "kwan_hui_lim"])
def test_a_handle_shaped_id_is_warned_about(client, row_id) -> None:
    body = run(client, [row_id])
    assert body["warnings"]
    assert row_id in body["warnings"][0]


@pytest.mark.parametrize("row_id", ["1", "sg-explicit-1", "hard-sem-3", "row12"])
def test_an_ordinary_id_is_not(client, row_id) -> None:
    assert run(client, [row_id])["warnings"] == []


def test_the_warning_counts_them_and_names_a_few(client) -> None:
    body = run(client, ["@a", "@b", "@c", "@d", "ordinary-1"])
    assert "4 row id(s)" in body["warnings"][0]
