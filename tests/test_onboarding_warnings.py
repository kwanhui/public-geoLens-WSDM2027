"""Tests for the onboarding profile validator (profile_warnings)."""

from __future__ import annotations

from geolens.onboarding import CityProfile, profile_warnings


def test_warnings_flag_missing_fields_and_stub():
    w = profile_warnings(CityProfile(name="Obscureville", source="stub"))
    assert any("stub" in x for x in w)
    assert any("aliases" in x for x in w)
    assert any("landmarks" in x for x in w)
    assert any("centroid" in x for x in w)


def test_clean_profile_has_no_warnings():
    p = CityProfile(
        name="Tengah", aliases=["Tengah"], landmarks=["Plantation Plaza", "Forest Drive"],
        foods=["kopi"], slang=["lah"], lat=1.36, lon=103.74, source="openai",
    )
    assert profile_warnings(p) == []


def test_out_of_range_centroid_flagged():
    p = CityProfile(name="X", aliases=["X"], landmarks=["A"], lat=999.0, lon=0.0, source="openai")
    assert any("out of range" in x for x in profile_warnings(p))
