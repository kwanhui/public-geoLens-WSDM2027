"""Adding an engine, and what a client is told about the ones there are.

The toy engine here is registered the way docs/adding-an-engine.md says to
and is not in the shipped roster.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from geolens.engines import registry
from geolens.engines.base import Engine, GeolocateInput, Prediction
from geolens.engines.registry import (
    ENGINE_SPECS,
    EngineSpec,
    build_engines,
    engine_metadata,
    local_engine_names,
)
from geolens.ui.server import create_app

SHIPPED = {
    "contrastgeo",
    "fewuser",
    "retrievezero",
    "gazetteer_post",
    "gazetteer_user",
    "gpt4o_mini_post",
    "gpt4o_mini_user",
    "claude_haiku_post",
    "claude_haiku_user",
}


class ToyEngine(Engine):
    """The smallest adapter that satisfies the contract."""

    name = "toy_nearest"
    granularity = "post"
    needs_credentials = False
    calls_a_third_party = False

    def __init__(self, *, cities: list[str], stub: bool | None = None) -> None:
        super().__init__(stub=stub)
        self.cities = cities

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        text = (payload.post or "").lower()
        hits = [c for c in self.cities if c.lower() in text]
        if not hits:
            return Prediction(city="", confidence=0.0, top_k=[], note="real:toy_nearest",
                              abstain=True)
        top_k = [(c, 1.0 / len(hits)) for c in hits[:k]]
        return Prediction(
            city=top_k[0][0],
            confidence=top_k[0][1],
            top_k=top_k,
            note="real:toy_nearest",
            evidence=f"matched: {top_k[0][0]}",
        )


TOY_SPEC = EngineSpec(
    key="toy_nearest",
    label="Toy nearest",
    family="toy",
    granularity="post",
    tag="string match",
    tag_title="A test engine that answers with any catalogue name in the text.",
    factory=lambda cities: ToyEngine(cities=cities),
)


@pytest.fixture
def with_toy_engine(monkeypatch):
    """Register the toy engine for one test, the way an adopter would."""
    monkeypatch.setattr(registry, "ENGINE_SPECS", (*ENGINE_SPECS, TOY_SPEC))
    monkeypatch.setitem(registry.SPECS_BY_KEY, "toy_nearest", TOY_SPEC)


@pytest.fixture
def client(monkeypatch, tmp_path, with_toy_engine):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


# ----- the shipped roster -----------------------------------------------------

def test_the_shipped_roster_is_a_superset_of_the_nine(monkeypatch, tmp_path) -> None:
    """A superset, not the literal nine: an added engine must not fail a test."""
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    engines, _ = build_engines()
    assert set(engines) >= SHIPPED


def test_one_roster_serves_the_server_and_the_cli() -> None:
    from geolens.cli import _cli_engines

    server_engines, _ = build_engines()
    cli_engines, _ = _cli_engines()
    assert list(cli_engines) == list(server_engines)


# ----- an added engine is visible everywhere ----------------------------------

def test_an_added_engine_is_in_the_roster(with_toy_engine) -> None:
    engines, catalogue = build_engines()
    assert "toy_nearest" in engines
    # It is handed the same mutable catalogue as everything else.
    assert engines["toy_nearest"].cities is catalogue


def test_an_added_engine_answers_a_query(client) -> None:
    body = client.post("/geolocate", json={"post": "Fire at the Bedok hawker centre"}).json()
    pred = body["per_engine"]["toy_nearest"]
    assert pred["city"] == "Bedok"
    assert pred["mode"] == "real"
    assert pred["place_id"]


def test_an_added_engine_abstains_the_way_the_contract_says(client) -> None:
    body = client.post("/geolocate", json={"post": "nothing here names a place"}).json()
    pred = body["per_engine"]["toy_nearest"]
    assert pred["abstain"] is True
    assert pred["city"] == ""
    assert pred["place_id"] is None


def test_an_added_engine_is_skipped_when_the_input_has_nothing_for_it(client) -> None:
    body = client.post("/geolocate", json={"user_posts": ["ramen in Shibuya"]}).json()
    pred = body["per_engine"]["toy_nearest"]
    assert pred["skipped"] is True
    assert pred["reason"] == "no post supplied"
    assert pred["mode"] == "skipped"


def test_an_added_engine_can_be_selected_and_deselected(client) -> None:
    body = client.post("/geolocate", json={
        "post": "Fire at Bedok", "engines": ["toy_nearest"],
    }).json()
    assert body["manifest"]["selected_engines"] == ["toy_nearest"]
    assert body["per_engine"]["gazetteer_post"]["reason"] == "not selected"


# ----- the metadata a client needs --------------------------------------------

def test_the_instance_describes_every_engine(client) -> None:
    info = client.get("/instance").json()["engines"]
    assert set(info) >= SHIPPED | {"toy_nearest"}
    for name, entry in info.items():
        assert entry["label"], name
        assert entry["granularity"] in ("post", "user"), name
        assert entry["tag"], name
        assert entry["tag_title"], name
        assert isinstance(entry["calls_a_third_party"], bool), name


def test_the_instance_names_the_engines_that_send_nothing_off_the_server(client) -> None:
    body = client.get("/instance").json()
    assert set(body["local_engines"]) == {
        "contrastgeo", "fewuser", "retrievezero",
        "gazetteer_post", "gazetteer_user", "toy_nearest",
    }
    for name in ("gpt4o_mini_post", "claude_haiku_user"):
        assert body["engines"][name]["calls_a_third_party"] is True


def test_the_labels_are_the_ones_the_interface_prints(client) -> None:
    info = client.get("/instance").json()["engines"]
    assert info["contrastgeo"]["label"] == "ContrastGeo"
    assert info["gazetteer_post"]["label"] == "Gazetteer (post)"
    assert info["claude_haiku_user"]["label"] == "Claude Haiku (user)"
    assert info["contrastgeo"]["tag"] == "frozen encoder"
    assert "No support examples" in info["fewuser"]["tag_title"]


def test_an_engine_the_roster_does_not_describe_is_still_described() -> None:
    """A fork that builds its own dict gets a sane entry, not a crash."""
    engines, catalogue = build_engines()
    engines["unregistered"] = ToyEngine(cities=catalogue)
    meta = engine_metadata(engines)
    assert meta["unregistered"]["label"] == "unregistered"
    assert meta["unregistered"]["granularity"] == "post"
    assert meta["unregistered"]["calls_a_third_party"] is False
    assert "unregistered" in local_engine_names(engines)
