"""Tests for the reproducibility run manifest."""

from __future__ import annotations

from geolens.manifest import build_manifest, catalogue_hash


class _FakeLLM:
    model = "gpt-4o-mini"


class _FakeEncoder:
    encoder = "intfloat/e5-large"


class _FakeRule:
    pass


def test_catalogue_hash_is_order_independent():
    assert catalogue_hash(["Tokyo", "Singapore"]) == catalogue_hash(["singapore", "TOKYO"])
    assert catalogue_hash(["Tokyo"]) != catalogue_hash(["Osaka"])


def test_manifest_captures_models_and_params():
    engines = {"llm": _FakeLLM(), "enc": _FakeEncoder(), "gz": _FakeRule()}
    m = build_manifest(engines, ["Tokyo", "Singapore"], k=5, ensemble_method="rrf")
    assert m["tool"] == "GeoLens"
    assert m["ensemble_method"] == "rrf"
    assert m["k"] == 5
    assert m["catalogue_size"] == 2
    assert m["engines"] == {
        "llm": "gpt-4o-mini",
        "enc": "intfloat/e5-large",
        "gz": "rule-based",
    }
    assert len(m["catalogue_sha"]) == 12
    assert m["generated_at"].endswith("+00:00")  # UTC, ISO-8601
