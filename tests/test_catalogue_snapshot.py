"""A request sees one catalogue from its first engine call through to its manifest.

The read side of the catalogue lock is held for the whole run, so an
onboarding arriving mid-request waits.
"""

from __future__ import annotations

import threading
import time

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.manifest import catalogue_hash
from geolens.onboarding.lock import CatalogueLock
from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_readers_run_together() -> None:
    lock = CatalogueLock()
    both_inside = threading.Event()
    first_inside = threading.Event()

    def reader() -> None:
        with lock.read():
            first_inside.set()
            both_inside.wait(2.0)

    t = threading.Thread(target=reader)
    t.start()
    assert first_inside.wait(2.0)
    with lock.read():
        both_inside.set()
    t.join(2.0)
    assert not t.is_alive()


def test_a_writer_waits_for_the_reader() -> None:
    lock = CatalogueLock()
    order: list[str] = []
    reading = threading.Event()

    def writer() -> None:
        with lock.write():
            order.append("write")

    with lock.read():
        t = threading.Thread(target=writer)
        t.start()
        reading.set()
        time.sleep(0.05)
        order.append("read")
    t.join(2.0)
    assert order == ["read", "write"]


def test_the_manifest_hashes_the_catalogue_the_run_was_given(client) -> None:
    body = client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"}).json()
    manifest = body["manifest"]
    assert manifest["catalogue_sha"] == catalogue_hash(list(DEFAULT_CITIES))
    assert manifest["catalogue_size"] == len(DEFAULT_CITIES)


def test_a_batch_row_carries_the_catalogue_it_was_run_against(client) -> None:
    body = client.post(
        "/batch_predict",
        json={"inputs": [{"id": "1", "post": "Queue at the Bedok hawker centre"}]},
    ).json()
    sha = body["manifest"]["catalogue_sha"]
    assert sha
    assert all(row["catalogue_sha"] == sha for row in body["rows"])
