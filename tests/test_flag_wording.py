"""What the verification flag says, and the vote behind each consensus place."""

from __future__ import annotations

from geolens.engines.base import Prediction
from geolens.triangulator import triangulate

GRANULARITY = {
    "gz_post": "post",
    "llm_post": "post",
    "gz_user": "user",
    "llm_user": "user",
}


def _pred(city: str, confidence: float = 0.9) -> Prediction:
    return Prediction(city=city, confidence=confidence, top_k=[(city, confidence)])


def _flagged():
    return triangulate(
        {
            "gz_post": _pred("Singapore"),
            "llm_post": _pred("Singapore"),
            "gz_user": _pred("Tokyo"),
            "llm_user": _pred("Jakarta", 0.4),
        },
        engines=GRANULARITY,
    )


def test_the_headline_says_what_each_side_read() -> None:
    note = _flagged().notes[0]
    assert note.startswith("the post text points to Singapore")
    assert "the account's timeline points to Tokyo" in note
    assert "km apart" in note


def test_the_post_being_about_another_place_is_a_candidate_cause() -> None:
    note = _flagged().notes[0]
    assert "one of the two predictions is wrong, which is the most common one" in note
    assert "the post is about another place" in note
    assert "a travel post" in note
    assert "shared or compromised account" in note
    assert "misleading geotag" in note


def test_each_consensus_carries_its_vote() -> None:
    tri = _flagged()
    assert (tri.post_consensus_votes, tri.post_consensus_answered) == (2, 2)
    assert (tri.user_consensus_votes, tri.user_consensus_answered) == (1, 2)


def test_the_vote_reaches_the_response(monkeypatch, tmp_path) -> None:
    from starlette.testclient import TestClient

    from geolens.ui.server import create_app

    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        tri = client.post("/geolocate", json={
            "post": "Fire at Marina Bay Sands in Singapore",
            "user_posts": ["Ramen in Tokyo", "Tokyo again"],
        }).json()["triangulation"]

    assert tri["post_consensus_answered"] >= 1
    assert tri["post_consensus_votes"] <= tri["post_consensus_answered"]
    assert tri["user_consensus_votes"] <= tri["user_consensus_answered"]


def test_a_near_miss_keeps_the_same_wording() -> None:
    tri = triangulate(
        {"p": _pred("Singapore"), "u": _pred("Bedok")},
        engines={"p": "post", "u": "user"},
    )
    assert tri.disagreement_flag is False
    assert tri.notes[0].startswith("the post text points to Singapore")
    assert "no flag is raised" in tri.notes[0]
