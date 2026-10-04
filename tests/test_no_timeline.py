"""Without a timeline the user-level engines are not called, and the response says so."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.batch import BatchInput, run_batch
from geolens.dispatch import NO_TIMELINE, run_engines
from geolens.engines import ContrastGeoEngine, FewUserEngine
from geolens.engines.base import GeolocateInput
from geolens.ui.server import create_app

USER_ENGINES = {"fewuser", "retrievezero", "gazetteer_user", "gpt4o_mini_user", "claude_haiku_user"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_user_level_engines_are_not_called_without_a_timeline() -> None:
    engines = {"contrastgeo": ContrastGeoEngine(stub=True), "fewuser": FewUserEngine(stub=True)}
    results = run_engines(engines, GeolocateInput(post="Queue at the Bedok hawker centre"), k=3)

    assert results["contrastgeo"].usable is True
    assert results["fewuser"].skipped is True
    assert results["fewuser"].mode == "skipped"
    assert results["fewuser"].reason == NO_TIMELINE
    assert results["fewuser"].city == ""


def test_user_level_engines_run_once_a_timeline_is_supplied() -> None:
    engines = {"fewuser": FewUserEngine(stub=True)}
    payload = GeolocateInput(post="a post", user_posts=["one", "two"])
    assert run_engines(engines, payload, k=3)["fewuser"].usable is True


def test_a_post_only_query_has_no_user_fusion_and_no_flag(client) -> None:
    body = client.post("/geolocate", json={"post": "Queue at the Bedok hawker centre"}).json()

    assert set(body["ensembles"]) == {"post"}
    assert body["triangulation"]["user_consensus_city"] == ""
    assert body["triangulation"]["disagreement_flag"] is False
    for name in USER_ENGINES:
        assert body["per_engine"][name]["skipped"] is True
        assert body["per_engine"][name]["reason"] == NO_TIMELINE
        assert body["manifest"]["engine_modes"][name] == "skipped"


def test_a_query_with_a_timeline_still_produces_both_levels(client) -> None:
    body = client.post(
        "/geolocate",
        json={"post": "Fire at Marina Bay Sands", "user_posts": ["ramen in Shibuya", "Asakusa"]},
    ).json()

    assert set(body["ensembles"]) == {"post", "user"}
    for name in USER_ENGINES:
        assert body["per_engine"][name]["skipped"] is False


def test_a_batch_row_without_a_timeline_scores_no_user_level_engine(client) -> None:
    rows = [BatchInput(id="1", post="Queue at the Bedok hawker centre", ground_truth_city="Bedok")]
    engines, catalogue = _roster()
    results = run_batch(rows, engines, catalogue=catalogue, k=3)

    assert set(results[0].ensembles) == {"post"}
    assert results[0].triangulation is not None
    assert results[0].triangulation.disagreement_flag is False
    assert results[0].per_engine["fewuser"].skipped is True


def _roster():
    from geolens.engines.registry import build_engines

    return build_engines()


def test_a_skipped_engine_is_not_scored_as_a_miss(client) -> None:
    """A user-level engine that never ran must not enter its Acc@1 denominator."""
    from geolens.batch.metrics import compute_summary

    engines, catalogue = _roster()
    rows = [
        BatchInput(id="1", post="Queue at the Bedok hawker centre", ground_truth_city="Bedok"),
        BatchInput(
            id="2",
            post="Queue at the Bedok hawker centre",
            user_posts=["Bedok again", "Bedok reservoir run"],
            ground_truth_city="Bedok",
        ),
    ]
    summary = compute_summary(run_batch(rows, engines, catalogue=catalogue, k=3))

    assert summary.per_engine["gazetteer_post"].n_evaluated == 2
    assert summary.per_engine["gazetteer_user"].n_evaluated == 1
