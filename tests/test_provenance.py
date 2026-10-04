"""What a manifest has to say for a result to be reproducible from it alone."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens import API_VERSION, __version__
from geolens.manifest import BUILD_COMMIT_FILE, _build_commit, git_commit
from geolens.pricing import PRICE_TABLE_RECORDED, PRICES
from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def single(client):
    return client.post("/geolocate", json={
        "post": "Fire at Marina Bay Sands", "user_posts": ["ramen in Shibuya"],
    }).json()["manifest"]


@pytest.fixture
def batch(client):
    return client.post("/eval", json={"inputs": [
        {"id": "1", "post": "Queue at Bedok", "ground_truth_city": "Bedok"},
    ]}).json()["manifest"]


# ----- which build produced it ------------------------------------------------

def test_a_manifest_names_the_build_and_the_contract(single) -> None:
    assert single["version"] == __version__
    assert single["api_version"] == API_VERSION


def test_a_manifest_names_the_commit_when_there_is_one(single) -> None:
    """A checkout answers from git; a build with neither says null."""
    commit = single["git_commit"]
    assert commit == git_commit()
    if commit is not None:
        assert len(commit) == 40
        assert isinstance(single["git_dirty"], bool)


def test_an_image_reports_the_commit_baked_in_at_build_time(monkeypatch) -> None:
    """The container has no .git, so the build writes the commit instead."""
    monkeypatch.setenv("GEOLENS_GIT_COMMIT", "a" * 40)
    assert _build_commit() == "a" * 40

    monkeypatch.delenv("GEOLENS_GIT_COMMIT")
    monkeypatch.setattr("geolens.manifest.BUILD_COMMIT_FILE", BUILD_COMMIT_FILE)
    assert _build_commit() is None or len(_build_commit()) == 40


def test_the_baked_commit_is_read_from_the_package(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GEOLENS_GIT_COMMIT", raising=False)
    baked = tmp_path / "build_commit.txt"
    baked.write_text("b" * 40 + "\n")
    monkeypatch.setattr("geolens.manifest.BUILD_COMMIT_FILE", baked)
    assert _build_commit() == "b" * 40


# ----- enough to recompute a distance -----------------------------------------

def test_a_manifest_carries_the_built_in_coordinates(single) -> None:
    built_in = {p["name"]: p for p in single["catalogue"]["built_in"]}
    assert built_in["Bedok"]["lat"] == pytest.approx(1.3236)
    assert built_in["Bedok"]["lon"] == pytest.approx(103.9273)
    assert built_in["Bedok"]["place_id"].startswith("pl_")
    assert built_in["Bedok"]["feature_type"] == "estate"
    assert built_in["Bedok"]["centroid_source"] == "seed-approximate"
    assert all(p["lat"] is not None for p in single["catalogue"]["built_in"])


def test_a_manifest_says_how_a_distance_is_measured(single) -> None:
    assert "haversine" in single["distance_method"]
    assert "6371.0088" in single["distance_method"]
    assert "ellipsoidal" in single["distance_method"]
    assert single["catalogue"]["distance_method"] == single["distance_method"]


def test_the_plain_list_of_built_in_names_is_still_there(single) -> None:
    assert "Singapore" in single["catalogue"]["built_in_names"]
    assert len(single["catalogue"]["built_in_names"]) == len(single["catalogue"]["built_in"])


# ----- a single query records what a batch does -------------------------------

def test_a_single_query_carries_the_call_counts(single) -> None:
    counts = single["engine_call_counts"]
    assert set(counts) == set(single["engines"])
    for per_engine in counts.values():
        assert sum(per_engine.values()) == 1


def test_a_single_query_carries_the_gazetteer_abstentions(single) -> None:
    abstained = single["n_gazetteer_abstained"]
    matched = single["n_gazetteer_matched"]
    assert set(abstained) == {"post", "user"}
    for level in ("post", "user"):
        assert abstained[level] + matched[level] <= 1


def test_a_batch_still_carries_both(batch) -> None:
    assert batch["engine_call_counts"]
    assert set(batch["n_gazetteer_abstained"]) == {"post", "user"}


# ----- the scheme the caller used ---------------------------------------------

def test_the_base_url_reports_the_scheme_the_caller_used(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("GEOLENS_TRUSTED_PROXY_HOPS", "1")
    with TestClient(create_app()) as client:
        body = client.post(
            "/geolocate",
            json={"post": "a post"},
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"},
        ).json()
    assert body["manifest"]["base_url"].startswith("https://")


def test_an_untrusted_forwarded_scheme_is_ignored(monkeypatch, tmp_path) -> None:
    """With no trusted proxy the header is a client-supplied string."""
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("GEOLENS_TRUSTED_PROXY_HOPS", "0")
    with TestClient(create_app()) as client:
        body = client.post(
            "/geolocate", json={"post": "a post"},
            headers={"X-Forwarded-Proto": "https"},
        ).json()
    assert body["manifest"]["base_url"].startswith("http://")


# ----- cost is an estimate ----------------------------------------------------

def test_a_manifest_records_the_prices_the_cost_was_estimated_from(single) -> None:
    table = single["price_table"]
    assert table["recorded_on"] == PRICE_TABLE_RECORDED
    assert set(table["usd_per_token"]) == set(PRICES)
    assert table["usd_per_token"]["gpt-4o-mini"]["input"] == PRICES["gpt-4o-mini"][0]
    assert "estimated" in table["note"]


def test_every_rate_carries_the_date_it_was_checked_and_its_source(single) -> None:
    for model, entry in single["price_table"]["usd_per_token"].items():
        assert entry["checked"], model
        assert entry["source"].startswith("https://"), model


def test_the_rates_are_the_providers_list_prices() -> None:
    """Per million tokens, as the pricing pages print them."""
    def per_mtok(model: str) -> tuple[float, float]:
        return (round(PRICES[model][0] * 1e6, 4), round(PRICES[model][1] * 1e6, 4))

    assert per_mtok("gpt-4o-mini") == (0.15, 0.60)
    assert per_mtok("gpt-4o") == (2.50, 10.00)
    # 1.00 and 5.00, not 0.80 and 4.00: the table held the Claude Haiku 3.5
    # price for 4.5, so every Claude cost was 0.8 of the correct figure.
    assert per_mtok("claude-haiku-4-5-20251001") == (1.00, 5.00)
    assert per_mtok("claude-sonnet-4-6") == (3.00, 15.00)


def test_an_unlisted_model_reports_no_cost_rather_than_a_wrong_one() -> None:
    from geolens.pricing import estimate_cost

    assert estimate_cost("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.00)
    assert estimate_cost("claude-haiku-4-5-20251001", 0, 1_000_000) == pytest.approx(5.00)
    # No cost at all, rather than the cheapest listed model's rates.
    assert estimate_cost("some-model-nobody-listed", 1_000_000, 1_000_000) is None


def test_a_call_that_cost_nothing_claims_no_estimate(client) -> None:
    """In placeholder mode no model is called, so no cost is estimated."""
    body = client.post("/geolocate", json={"post": "a post"}).json()
    for name, pred in body["per_engine"].items():
        assert pred["cost_usd"] == 0.0, name
        assert pred["cost_is_estimated"] is False, name


# ----- the mean-rank sentinel is documented -----------------------------------

def test_the_schema_documents_the_mean_rank_sentinel(client) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    description = schemas["EngineMetricsView"]["properties"]["mean_rank"]["description"]
    assert "1,000,000" in description
    assert "mean_rank_found" in description
    for field in ("mean_rank_found", "n_rank_found"):
        assert field in schemas["EngineMetricsView"]["properties"]


def test_the_additive_fields_are_what_the_docs_recommend(batch, client) -> None:
    body = client.post("/eval", json={"inputs": [
        {"id": "1", "post": "Queue at Bedok", "ground_truth_city": "Bedok"},
        {"id": "2", "post": "nothing here", "ground_truth_city": "Tokyo"},
    ]}).json()
    for metrics in body["summary"]["per_engine"].values():
        assert metrics["n_rank_found"] <= metrics["n_evaluated"]
        if metrics["n_rank_found"]:
            # A found rank is a real position in the list, never the sentinel.
            assert 1 <= metrics["mean_rank_found"] <= 20
