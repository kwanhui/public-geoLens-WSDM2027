from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from geolens.spend import SpendLedger
from geolens.ui.limits import MAX_BATCH_CHARS, MAX_POST_CHARS
from geolens.ui.server import SPEND_CEILING_SKIP, create_app

PAID_ENGINES = (
    "gpt4o_mini_post",
    "gpt4o_mini_user",
    "claude_haiku_post",
    "claude_haiku_user",
)


def make_client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app())


@pytest.fixture
def client(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as c:
        yield c


# ----- the ledger -------------------------------------------------------------

def test_the_ledger_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GEOLENS_MAX_USD_PER_HOUR", raising=False)
    monkeypatch.delenv("GEOLENS_MAX_USD_PER_DAY", raising=False)
    ledger = SpendLedger()
    ledger.record(100.0)
    assert ledger.state().ceiling_reached is False


def test_the_hourly_ceiling_bites_and_the_daily_total_outlives_it(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_MAX_USD_PER_HOUR", "2")
    monkeypatch.setenv("GEOLENS_MAX_USD_PER_DAY", "10")
    ledger = SpendLedger()
    ledger.record(2.5, now=0.0)

    hot = ledger.state(now=60.0)
    assert hot.ceiling_reached is True
    assert "hourly ceiling" in hot.reason

    later = ledger.state(now=7200.0)
    assert later.estimated_usd_last_hour == 0.0
    assert later.estimated_usd_last_day == 2.5
    assert later.ceiling_reached is False


def test_the_daily_ceiling_bites_on_its_own(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_MAX_USD_PER_DAY", "1")
    ledger = SpendLedger()
    ledger.record(1.5, now=0.0)
    state = ledger.state(now=7200.0)
    assert state.ceiling_reached is True
    assert "daily ceiling" in state.reason


# ----- what the endpoints do about it -----------------------------------------

def test_the_paid_engines_are_skipped_while_the_ceiling_is_reached(
    monkeypatch, tmp_path
) -> None:
    with make_client(monkeypatch, tmp_path, GEOLENS_MAX_USD_PER_HOUR="1") as client:
        client.app.state.spend.record(5.0)
        body = client.post(
            "/geolocate",
            json={"post": "Fire at Marina Bay Sands", "user_posts": ["ramen in Shibuya"]},
        ).json()

    assert body["spend"]["ceiling_reached"] is True
    assert "hourly ceiling" in body["spend"]["reason"]
    for name in PAID_ENGINES:
        assert body["per_engine"][name]["skipped"] is True
        assert body["per_engine"][name]["reason"] == SPEND_CEILING_SKIP
    # The engines that run on this server keep answering.
    assert body["per_engine"]["gazetteer_post"]["skipped"] is False


def test_a_bulk_run_skips_the_paid_engines_too(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path, GEOLENS_MAX_USD_PER_DAY="1") as client:
        client.app.state.spend.record(5.0)
        body = client.post(
            "/batch_predict",
            json={"inputs": [{"id": "1", "post": "Queue at the Bedok hawker centre"}]},
        ).json()
    row = body["rows"][0]
    assert row["per_engine"]["claude_haiku_post"]["skipped"] is True
    assert row["per_engine"]["gazetteer_post"]["skipped"] is False
    assert body["spend"]["ceiling_reached"] is True


def test_the_instance_endpoint_reports_the_ceiling(monkeypatch, tmp_path) -> None:
    with make_client(
        monkeypatch, tmp_path, GEOLENS_MAX_USD_PER_HOUR="2", GEOLENS_MAX_USD_PER_DAY="10"
    ) as client:
        spend = client.get("/instance").json()["spend"]
    assert spend["max_usd_per_hour"] == 2.0
    assert spend["max_usd_per_day"] == 10.0
    assert spend["ceiling_reached"] is False
    assert spend["reason"] == ""


def test_a_query_response_carries_the_spend_state(client) -> None:
    body = client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"}).json()
    assert body["spend"]["ceiling_reached"] is False
    assert body["spend"]["max_usd_per_hour"] == 0.0


# ----- request bounds ---------------------------------------------------------

def test_a_json_batch_body_over_the_byte_cap_is_refused(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path, MAX_BATCH_BYTES="2000") as client:
        rows = [{"id": str(i), "post": "x" * MAX_POST_CHARS} for i in range(5)]
        resp = client.post("/batch_predict", json={"inputs": rows})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "body_too_large"


def test_a_json_batch_over_the_character_cap_is_refused(monkeypatch, tmp_path) -> None:
    with make_client(
        monkeypatch, tmp_path, MAX_BATCH_BYTES="100000000", MAX_BATCH_ROWS="500"
    ) as client:
        rows = [
            {"id": str(i), "post": "x" * MAX_POST_CHARS}
            for i in range(MAX_BATCH_CHARS // MAX_POST_CHARS + 2)
        ]
        resp = client.post("/batch_predict", json={"inputs": rows})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "too_many_characters"


def test_the_instance_endpoint_states_every_batch_cap(client) -> None:
    body = client.get("/instance").json()
    assert body["max_batch_bytes"] == 200_000
    assert body["max_batch_chars"] == MAX_BATCH_CHARS


def test_an_empty_rate_limit_bucket_is_dropped(monkeypatch, tmp_path) -> None:
    with make_client(monkeypatch, tmp_path, MAX_QUERIES_PER_HOUR="5") as client:
        buckets = client.app.state.rate_limit_buckets
        buckets["queries"]["198.51.100.7"].append(time.time() - 7200)
        client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"})
        assert "198.51.100.7" not in buckets["queries"]
