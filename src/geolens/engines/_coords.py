"""Stored (lat, lon) points for the built-in catalogue, and what each one is.

Beside each name, ``PLACE_FEATURES`` records a ``feature_type`` (country down
to street) and a ``centroid_source``, both served by ``GET /catalogue``: the 28
WNUT-2016 places carry the shared task's gold city centres and the 22 seed
places hand-entered approximate points. Every coordinate is written to four
decimal places, and distances come from ``geolens.geo.haversine_km``.
``eval/README.md`` has the provenance in full.
"""

from __future__ import annotations

from geolens.places import name_key

CITY_COORDS: dict[str, tuple[float, float]] = {
    "Singapore": (1.3521, 103.8198),
    "Tengah Plantation Crescent": (1.3608, 103.7382),
    "Tampines": (1.3496, 103.9568),
    "Jurong East": (1.3329, 103.7436),
    "Punggol": (1.4041, 103.9025),
    "Bedok": (1.3236, 103.9273),
    "Woodlands": (1.4382, 103.7891),
    "Kuala Lumpur": (3.1390, 101.6869),
    "Petaling Jaya": (3.1073, 101.6067),
    "Jakarta": (-6.2088, 106.8456),
    "Pekanbaru": (0.5071, 101.4478),
    "Bangkok": (13.7563, 100.5018),
    "Manila": (14.5995, 120.9842),
    "Ho Chi Minh City": (10.8231, 106.6297),
    "Hong Kong": (22.3193, 114.1694),
    "Tokyo": (35.6762, 139.6503),
    "Seoul": (37.5665, 126.9780),
    "Sydney": (-33.8688, 151.2093),
    "London": (51.5074, -0.1278),
    "New York": (40.7128, -74.0060),
    "San Francisco": (37.7749, -122.4194),
    "Toronto": (43.6532, -79.3832),
    # WNUT-2016 metros (centroids from the benchmark's gold city centres).
    "Los Angeles": (34.0522, -118.2437),
    "Bandung": (-6.9039, 107.6186),
    "Istanbul": (41.0138, 28.9497),
    "Chicago": (41.8500, -87.6500),
    "Sao Paulo": (-23.5475, -46.6361),
    "Rio de Janeiro": (-22.9028, -43.2075),
    "Denpasar": (-8.6500, 115.2167),
    "Surabaya": (-7.2492, 112.7508),
    "Atlanta": (33.7490, -84.3880),
    "Medan": (3.5833, 98.6667),
    "Dallas": (32.7831, -96.8067),
    "Miami": (25.7743, -80.1937),
    "Izmir": (38.4127, 27.1384),
    "Makassar": (-5.1400, 119.4221),
    "Las Vegas": (36.1750, -115.1372),
    "Lagos": (6.4531, 3.3958),
    "Yogyakarta": (-7.7828, 110.3608),
    "Austin": (30.2672, -97.7431),
    "Malang": (-7.9797, 112.6304),
    "San Diego": (32.7153, -117.1573),
    "Dublin": (53.3331, -6.2489),
    "San Antonio": (29.4241, -98.4936),
    "Houston": (29.7633, -95.3633),
    "Buenos Aires": (-34.6131, -58.3772),
    "Cleveland": (41.4995, -81.6954),
    "Philadelphia": (39.9523, -75.1638),
    "Curitiba": (-25.4278, -49.2731),
    "Charlotte": (35.2271, -80.8431),
}

SEED_SOURCE = "seed-approximate"
WNUT_SOURCE = "wnut2016-gold-city-centre"

# name -> (feature_type, centroid_source). feature_type is one of country,
# city, town, estate, street.
PLACE_FEATURES: dict[str, tuple[str, str]] = {
    "Singapore": ("country", SEED_SOURCE),
    "Tengah Plantation Crescent": ("street", SEED_SOURCE),
    "Tampines": ("estate", SEED_SOURCE),
    "Jurong East": ("estate", SEED_SOURCE),
    "Punggol": ("estate", SEED_SOURCE),
    "Bedok": ("estate", SEED_SOURCE),
    "Woodlands": ("estate", SEED_SOURCE),
    "Kuala Lumpur": ("city", SEED_SOURCE),
    "Petaling Jaya": ("city", SEED_SOURCE),
    "Jakarta": ("city", SEED_SOURCE),
    "Pekanbaru": ("city", SEED_SOURCE),
    "Bangkok": ("city", SEED_SOURCE),
    "Manila": ("city", SEED_SOURCE),
    "Ho Chi Minh City": ("city", SEED_SOURCE),
    "Hong Kong": ("city", SEED_SOURCE),
    "Tokyo": ("city", SEED_SOURCE),
    "Seoul": ("city", SEED_SOURCE),
    "Sydney": ("city", SEED_SOURCE),
    "London": ("city", SEED_SOURCE),
    "New York": ("city", SEED_SOURCE),
    "San Francisco": ("city", SEED_SOURCE),
    "Toronto": ("city", SEED_SOURCE),
}
PLACE_FEATURES.update(
    {name: ("city", WNUT_SOURCE) for name in CITY_COORDS if name not in PLACE_FEATURES}
)

ONBOARDED_FEATURE = "onboarded-unverified"

FEATURE_TYPES = ("country", "city", "town", "estate", "street", ONBOARDED_FEATURE)


def coords_for(city: str | None) -> tuple[float, float] | None:
    """Coordinate lookup on the normalised, case-folded place name.

    Falls back to a coordinate captured during cold-start onboarding, so a
    freshly onboarded place pins on the map and enters the distance metrics
    instead of being silently dropped. None only if the place is unknown and
    has no onboarded coordinate.
    """
    if not city:
        return None
    target = name_key(city)
    for name, latlon in CITY_COORDS.items():
        if name_key(name) == target:
            return latlon
    # Onboarded places are not in the built-in table; consult their profile.
    from geolens.onboarding.wizard import onboarded_coords

    return onboarded_coords(city)


def feature_for(place: str | None) -> tuple[str | None, str | None]:
    """(feature_type, centroid_source) for a place.

    A built-in place carries the scale it is and where its point came from.
    A place onboarded at run time carries ``onboarded-unverified`` and no
    centroid source: the operator supplies the coordinate and nothing records
    the scale of the place or checks the point. Both are None for a place
    outside the catalogue, such as an out-of-catalogue ground truth.
    """
    if not place:
        return None, None
    target = name_key(place)
    for name, meta in PLACE_FEATURES.items():
        if name_key(name) == target:
            return meta
    from geolens.onboarding.wizard import cached_profile

    if cached_profile(place) is not None:
        return ONBOARDED_FEATURE, None
    return None, None
