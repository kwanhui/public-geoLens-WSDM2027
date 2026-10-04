"""Cold-start city onboarding via LLM-generated Modular Retrieval profiles."""

from geolens.onboarding.catalogue import (
    CatalogueChange,
    OnboardingRegistry,
    find_in_catalogue,
    is_default_city,
    max_onboarded,
    ttl_minutes,
)
from geolens.onboarding.proximity import nearest_catalogue_place, proximity_warning
from geolens.onboarding.regions import region_mismatch, resolve_country
from geolens.onboarding.validation import (
    ProfileError,
    validate_coordinate,
    validate_field_text,
    validate_list,
    validate_notes,
    validate_place_name,
    validate_region,
)
from geolens.onboarding.wizard import (
    CityProfile,
    cached_profile,
    forget_profile,
    onboard_city,
    onboarded_coords,
    profile_warnings,
    save_profile,
)

__all__ = [
    "CatalogueChange",
    "CityProfile",
    "OnboardingRegistry",
    "ProfileError",
    "cached_profile",
    "find_in_catalogue",
    "forget_profile",
    "is_default_city",
    "max_onboarded",
    "nearest_catalogue_place",
    "onboard_city",
    "onboarded_coords",
    "profile_warnings",
    "proximity_warning",
    "region_mismatch",
    "resolve_country",
    "save_profile",
    "ttl_minutes",
    "validate_coordinate",
    "validate_field_text",
    "validate_list",
    "validate_notes",
    "validate_place_name",
    "validate_region",
]
