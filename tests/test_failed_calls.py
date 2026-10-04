"""A failed engine call must not pass for a prediction.

The three places where that matters are the fusion, the per-level consensus
behind the verification flag, and the run manifest.
"""

from __future__ import annotations

from geolens.engines._reply_json import extract_json_object
from geolens.engines.base import Prediction, failed_prediction
from geolens.ensemble import ensemble
from geolens.manifest import build_manifest, call_counts
from geolens.triangulator import triangulate

GRANULARITIES = {"a_post": "post", "b_post": "post", "a_user": "user", "b_user": "user"}


def _ok(city: str, confidence: float) -> Prediction:
    return Prediction(
        city=city,
        confidence=confidence,
        top_k=[(city, confidence)],
        note="real:test",
    )


def test_a_failed_prediction_carries_no_city() -> None:
    pred = failed_prediction("llm_claude_haiku", error_class="JSONDecodeError")

    assert pred.city == ""
    assert pred.top_k == []
    assert pred.mode == "failed"
    assert pred.usable is False
    assert "JSONDecodeError" in pred.note


def test_a_failed_call_is_left_out_of_the_fusion() -> None:
    per_engine = {
        "a_post": _ok("Singapore", 0.8),
        "b_post": failed_prediction("b_post", error_class="APIStatusError"),
    }
    result = ensemble(per_engine, GRANULARITIES, target="post")

    assert result is not None
    assert result.contributing_engines == ["a_post"]
    assert result.consensus_city == "Singapore"


def test_a_failed_call_cannot_move_the_consensus_or_the_flag() -> None:
    """Without the fix the failed engine's placeholder city set the consensus."""
    per_engine = {
        "a_post": _ok("Singapore", 0.8),
        "a_user": _ok("Singapore", 0.7),
        "b_user": failed_prediction("b_user", error_class="JSONDecodeError"),
    }
    tri = triangulate(per_engine, engines=GRANULARITIES)

    assert tri.post_consensus_city == "Singapore"
    assert tri.user_consensus_city == "Singapore"
    assert tri.disagreement_flag is False
    # The failed engine is still reported, it just does not vote.
    assert "b_user" in tri.per_engine


def test_every_engine_failing_leaves_no_consensus() -> None:
    per_engine = {
        "a_post": failed_prediction("a_post", error_class="APIConnectionError"),
        "a_user": failed_prediction("a_user", error_class="APIConnectionError"),
    }
    tri = triangulate(per_engine, engines=GRANULARITIES)

    assert tri.consensus_city == ""
    assert tri.disagreement_flag is False
    assert tri.notes == ["no engine returned a prediction"]


def test_the_manifest_reports_what_each_call_did() -> None:
    per_engine = {
        "a_post": _ok("Singapore", 0.8),
        "b_post": Prediction(city="Tokyo", confidence=0.3, note="stub: b_post"),
        "a_user": failed_prediction("a_user", error_class="JSONDecodeError"),
    }
    engines = {name: object() for name in per_engine}
    manifest = build_manifest(
        engines, ["Singapore"], k=5, ensemble_method="weighted", predictions=per_engine
    )

    assert manifest["engine_modes"] == {
        "a_post": "real",
        "b_post": "stub",
        "a_user": "failed",
    }


def test_a_batch_manifest_counts_real_placeholder_and_failed_calls() -> None:
    rows = [
        {"a_post": _ok("Singapore", 0.8)},
        {"a_post": failed_prediction("a_post", error_class="JSONDecodeError")},
        {"a_post": Prediction(city="Tokyo", confidence=0.2, note="stub: a_post")},
    ]
    counts = call_counts(rows)
    manifest = build_manifest(
        {"a_post": object()}, ["Singapore"], k=5, ensemble_method="weighted", counts=counts
    )

    assert manifest["engine_call_counts"]["a_post"] == {
        "real": 1, "stub": 1, "failed": 1, "skipped": 0
    }
    assert manifest["engine_modes"]["a_post"] == "mixed"


def test_a_uniform_batch_reports_the_single_mode() -> None:
    rows = [{"a_post": _ok("Singapore", 0.8)}, {"a_post": _ok("Tokyo", 0.5)}]
    manifest = build_manifest(
        {"a_post": object()},
        ["Singapore"],
        k=5,
        ensemble_method="weighted",
        counts=call_counts(rows),
    )

    assert manifest["engine_modes"]["a_post"] == "real"


def test_json_is_read_out_of_a_fenced_or_prefixed_reply() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Sure. {"a": 1} Let me know.') == {"a": 1}
    assert extract_json_object('{"a": "}"}') == {"a": "}"}


def test_an_unreadable_reply_raises_rather_than_guessing() -> None:
    for text in ("", "no json here", "[1, 2, 3]"):
        try:
            extract_json_object(text)
        except ValueError:
            continue
        raise AssertionError(f"{text!r} should not parse to an object")
