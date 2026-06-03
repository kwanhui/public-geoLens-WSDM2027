"""Approximate (lat, lon) centroids for the built-in catalogue.

Used to turn city-label predictions into great-circle distance error, the
metric geolocation work actually reports (median/mean km, Acc@161km). These
mirror the coordinates the map UI uses (``app.js`` ``CITY_COORDS``); keep the
two in sync. Onboarded cities without a coordinate are handled gracefully by
the metrics layer (they are excluded from the distance denominator).
"""

from __future__ import annotations

CITY_COORDS: dict[str, tuple[float, float]] = {
    "Singapore": (1.3521, 103.8198),
    "Tengah Plantation Crescent": (1.3608, 103.7382),
    "Tampines": (1.3496, 103.9568),
    "Jurong East": (1.3329, 103.7436),
    "Punggol": (1.4041, 103.9025),
    "Bedok": (1.3236, 103.9273),
    "Woodlands": (1.4382, 103.7891),
    "Kuala Lumpur": (3.139, 101.6869),
    "Petaling Jaya": (3.1073, 101.6067),
    "Jakarta": (-6.2088, 106.8456),
    "Pekanbaru": (0.5071, 101.4478),
    "Bangkok": (13.7563, 100.5018),
    "Manila": (14.5995, 120.9842),
    "Ho Chi Minh City": (10.8231, 106.6297),
    "Hong Kong": (22.3193, 114.1694),
    "Tokyo": (35.6762, 139.6503),
    "Seoul": (37.5665, 126.978),
    "Sydney": (-33.8688, 151.2093),
    "London": (51.5074, -0.1278),
    "New York": (40.7128, -74.006),
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


def coords_for(city: str | None) -> tuple[float, float] | None:
    """Case-insensitive coordinate lookup.

    Falls back to a coordinate captured during cold-start onboarding, so a
    freshly onboarded city pins on the map and enters the distance metrics
    instead of being silently dropped. None only if the city is unknown and
    has no onboarded coordinate.
    """
    if not city:
        return None
    target = city.strip().lower()
    for name, latlon in CITY_COORDS.items():
        if name.lower() == target:
            return latlon
    # Onboarded cities are not in the built-in table; consult their profile.
    from geolens.onboarding.wizard import onboarded_coords

    return onboarded_coords(city)
