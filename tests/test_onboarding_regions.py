from __future__ import annotations

from geolens.onboarding import CityProfile, profile_warnings, region_mismatch, resolve_country
from geolens.onboarding.wizard import _stub_profile

# The coordinate GPT-4o-mini returned for "Bidadari Estate" with no hint.
JAKARTA_DRAFT = (-6.3005, 106.8467)
BIDADARI = (1.3396, 103.8720)


def test_a_bare_country_resolves() -> None:
    assert resolve_country("Singapore") == "Singapore"
    assert resolve_country("  indonesia ") == "Indonesia"


def test_a_narrow_to_wide_hint_resolves_on_its_last_part() -> None:
    assert resolve_country("West Kalimantan, Indonesia") == "Indonesia"
    assert resolve_country("Bukit Merah, SG") == "Singapore"


def test_an_unknown_region_resolves_to_nothing() -> None:
    assert resolve_country("Nordrhein-Westfalen") is None
    assert resolve_country("") is None


def test_a_coordinate_in_the_wrong_country_is_flagged() -> None:
    msg = region_mismatch("Singapore", *JAKARTA_DRAFT)
    assert msg is not None
    assert "outside Singapore" in msg


def test_a_coordinate_in_the_named_country_is_not_flagged() -> None:
    assert region_mismatch("Singapore", *BIDADARI) is None
    assert region_mismatch("West Kalimantan, Indonesia", -0.0736, 111.4954) is None


def test_no_hint_and_no_known_country_produce_no_warning() -> None:
    assert region_mismatch("", *JAKARTA_DRAFT) is None
    assert region_mismatch("Middle Earth", *JAKARTA_DRAFT) is None


def test_the_estate_draft_warns_when_the_hint_says_singapore() -> None:
    drafted = CityProfile(
        name="Bidadari Estate",
        aliases=["Bidadari"],
        landmarks=["Bidadari Park"],
        foods=["Nasi Padang"],
        lat=JAKARTA_DRAFT[0],
        lon=JAKARTA_DRAFT[1],
        region="Singapore",
        source="openai",
    )
    assert any("outside Singapore" in w for w in profile_warnings(drafted))


def test_the_same_draft_without_a_hint_is_not_second_guessed() -> None:
    drafted = CityProfile(
        name="Bidadari Estate",
        aliases=["Bidadari"],
        landmarks=["Bidadari Park"],
        lat=JAKARTA_DRAFT[0],
        lon=JAKARTA_DRAFT[1],
        source="openai",
    )
    warnings = profile_warnings(drafted)
    # No hint, so the rectangle test has nothing to check against. The only
    # warning left is the one saying a bare name is ambiguous.
    assert not any("outside" in w for w in warnings)
    assert warnings == [w for w in warnings if "no country or region hint" in w]


def test_the_stub_records_the_hint_without_inventing_a_coordinate() -> None:
    p = _stub_profile("Bidadari Estate", "Singapore")
    assert p.region == "Singapore"
    assert p.coords() is None
    assert "Singapore" in p.notes
    assert any("no centroid" in w for w in profile_warnings(p))
