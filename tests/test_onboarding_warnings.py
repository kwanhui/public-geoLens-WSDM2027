"""The onboarding profile validator, `profile_warnings`."""

from __future__ import annotations

from geolens.onboarding import CityProfile, profile_warnings


def test_warnings_flag_missing_fields_and_the_placeholder_draft():
    w = profile_warnings(CityProfile(name="Obscureville", source="stub"))
    assert any("placeholder" in x for x in w)
    assert any("aliases" in x for x in w)
    assert any("landmarks" in x for x in w)
    assert any("centroid" in x for x in w)


def test_the_alias_warning_says_what_the_gazetteer_will_still_match():
    """Without aliases the gazetteer matches the full name, not nothing."""
    w = profile_warnings(CityProfile(name="Kota Belud", landmarks=["A"], source="openai"))
    assert any("match only the full name" in x for x in w)


def test_the_landmark_warning_names_the_engine_that_reads_the_field():
    w = profile_warnings(CityProfile(name="Kota Belud", aliases=["Belud"], source="openai"))
    assert any("RetrieveZero" in x for x in w)


def test_a_bare_place_name_is_warned_about():
    """A draft for Cambridge with no hint merged two cities and warned nothing."""
    w = profile_warnings(CityProfile(name="Cambridge", aliases=["Cantab"], source="openai"))
    assert any("no country or region hint" in x for x in w)


def test_clean_profile_has_no_warnings():
    p = CityProfile(
        name="Tengah", aliases=["Tengah"], landmarks=["Plantation Plaza", "Forest Drive"],
        foods=["kopi"], slang=["lah"], lat=1.36, lon=103.74, region="Singapore",
        source="openai",
    )
    assert profile_warnings(p) == []


def test_out_of_range_centroid_flagged():
    p = CityProfile(
        name="X", aliases=["Xanadu"], landmarks=["A"], lat=999.0, lon=0.0,
        region="Singapore", source="openai",
    )
    assert any("out of range" in x for x in profile_warnings(p))
