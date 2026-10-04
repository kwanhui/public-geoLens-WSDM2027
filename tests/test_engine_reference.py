"""Each engine's reference accuracy, as the interface prints it.

The packaged copy and the committed WNUT-2016 result have to stay
byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from geolens.engine_reference import DATA_FILE, EVAL_TAG, engine_reference
from geolens.engines.registry import ENGINE_SPECS
from geolens.stats import wilson_interval
from geolens.ui.server import create_app

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "eval/results/wnut2016.json"

POST_LEVEL = ("contrastgeo", "gazetteer_post", "gpt4o_mini_post", "claude_haiku_post")


def test_the_packaged_copy_matches_the_committed_run() -> None:
    assert DATA_FILE.read_bytes() == COMMITTED.read_bytes()


def test_every_registered_engine_has_a_reference() -> None:
    reference = engine_reference()["engines"]
    assert set(reference) == {spec.key for spec in ENGINE_SPECS}


def test_the_rates_are_read_from_the_run_not_written_here() -> None:
    data = json.loads(COMMITTED.read_text())
    reference = engine_reference()["engines"]
    for level, granularity in (("post_level", "post"), ("user_level", "user")):
        for name, m in data[level]["engines"].items():
            entry = reference[name]
            assert entry["granularity"] == granularity
            assert entry["overall"]["acc_at_1"] == m["acc_at_1"]
            assert entry["overall"]["acc_at_1_ci"] == list(m["acc_at_1_ci"])
            assert entry["overall"]["n"] == m["n_evaluated"]


@pytest.mark.parametrize("name", POST_LEVEL)
def test_a_post_level_engine_carries_the_named_split(name) -> None:
    data = json.loads(COMMITTED.read_text())
    buckets = data["post_level"]["per_bucket"]
    entry = engine_reference()["engines"][name]

    assert entry["named"]["acc_at_1"] == buckets["intl"]["acc_at_1"][name]
    assert entry["named"]["n"] == buckets["intl"]["n_rows"]
    assert entry["unnamed"]["acc_at_1"] == buckets["hard-sem"]["acc_at_1"][name]
    assert entry["unnamed"]["n"] == buckets["hard-sem"]["n_rows"]


def test_a_split_carries_its_wilson_interval() -> None:
    entry = engine_reference()["engines"]["gazetteer_post"]["unnamed"]
    low, high = wilson_interval(round(entry["acc_at_1"] * entry["n"]), entry["n"])
    assert entry["acc_at_1_ci"] == [low, high]
    assert low < entry["acc_at_1"] < high or entry["acc_at_1"] in (0.0, 1.0)


def test_a_user_level_engine_carries_no_post_level_split() -> None:
    entry = engine_reference()["engines"]["fewuser"]
    assert "named" not in entry
    assert "unnamed" not in entry


def test_the_provenance_names_the_benchmark_and_the_tag() -> None:
    reference = engine_reference()
    assert reference["tag"] == EVAL_TAG
    assert reference["provenance"] == (
        "WNUT-2016 validation tweets, English, 50-place catalogue, tag "
        f"{EVAL_TAG}."
    )
    assert reference["catalogue_size"] == 50


def test_the_page_can_read_it_from_the_instance_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        body = client.get("/instance").json()["engine_reference"]
    assert body == engine_reference()
