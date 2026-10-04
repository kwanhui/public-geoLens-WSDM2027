from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.batch import BatchInput, run_batch
from geolens.dispatch import NO_POST, run_engines
from geolens.engines import ContrastGeoEngine, FewUserEngine
from geolens.engines.base import GeolocateInput
from geolens.engines.registry import build_engines
from geolens.ui.server import create_app

POST_ENGINES = {"contrastgeo", "gazetteer_post", "gpt4o_mini_post", "claude_haiku_post"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_post_level_engines_are_not_called_without_a_post() -> None:
    engines = {"contrastgeo": ContrastGeoEngine(stub=True), "fewuser": FewUserEngine(stub=True)}
    results = run_engines(engines, GeolocateInput(user_posts=["ramen in Shibuya"]), k=3)

    assert results["contrastgeo"].skipped is True
    assert results["contrastgeo"].reason == NO_POST
    assert results["contrastgeo"].city == ""
    assert results["fewuser"].usable is True


def test_a_timeline_only_query_has_no_post_fusion_and_no_flag(client) -> None:
    body = client.post(
        "/geolocate", json={"user_posts": ["ramen in Shibuya", "Asakusa again"]}
    ).json()

    assert set(body["ensembles"]) == {"user"}
    assert body["triangulation"]["post_consensus_city"] == ""
    assert body["triangulation"]["disagreement_flag"] is False
    for name in POST_ENGINES:
        assert body["per_engine"][name]["skipped"] is True
        assert body["per_engine"][name]["reason"] == NO_POST


def test_a_batch_row_without_a_post_scores_no_post_level_engine() -> None:
    engines, catalogue = build_engines()
    rows = [BatchInput(id="1", user_posts=["Bedok again"], ground_truth_city="Bedok")]
    results = run_batch(rows, engines, catalogue=catalogue, k=3)

    assert set(results[0].ensembles) == {"user"}
    assert results[0].per_engine["gazetteer_post"].skipped is True
    assert results[0].triangulation is not None
    assert results[0].triangulation.disagreement_flag is False
