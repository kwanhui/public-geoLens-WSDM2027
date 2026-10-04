"""A coarse plausibility check on a drafted centroid against a region hint.

The drafting model can place a small place in the wrong country and still
return a confident-looking profile. Asked for "Bidadari Estate" with no hint,
GPT-4o-mini calls it a residential area in Jakarta and returns (-6.3005,
106.8467).

The check is one rectangle per country, so a coordinate inside one is only
"not obviously wrong": the rectangle around Indonesia also contains part of
Malaysia and all of Timor-Leste, and the one around the United States is wide
enough for Alaska and Hawaii and so covers southern Canada and a stretch of
Pacific ocean. The table covers the countries the default catalogue draws from,
plus Indonesia and Singapore. A hint naming none of them produces no warning.
"""

from __future__ import annotations

import re

# country -> (min_lat, max_lat, min_lon, max_lon)
COUNTRY_BOXES: dict[str, tuple[float, float, float, float]] = {
    "Singapore": (1.13, 1.50, 103.59, 104.13),
    "Indonesia": (-11.11, 6.08, 94.93, 141.06),
    "Malaysia": (0.83, 7.40, 99.62, 119.28),
    "Thailand": (5.61, 20.47, 97.34, 105.65),
    "Philippines": (4.58, 21.13, 116.87, 126.61),
    "Vietnam": (8.17, 23.40, 102.14, 109.47),
    "Hong Kong": (22.14, 22.57, 113.82, 114.45),
    "China": (17.99, 53.57, 73.50, 134.78),
    "Japan": (24.04, 45.56, 122.93, 145.83),
    "South Korea": (33.10, 38.63, 124.60, 131.00),
    "Australia": (-43.65, -9.22, 112.92, 153.64),
    "United Kingdom": (49.86, 60.86, -8.65, 1.77),
    "Ireland": (51.42, 55.39, -10.56, -5.99),
    # Drawn wide enough to hold Alaska and Hawaii, so it is a weak check.
    "United States": (18.91, 71.39, -179.15, -66.95),
    "Canada": (41.68, 83.11, -141.00, -52.62),
    "Turkey": (35.81, 42.11, 25.66, 44.82),
    "Brazil": (-33.75, 5.27, -73.99, -34.79),
    "Argentina": (-55.06, -21.78, -73.58, -53.64),
    "Nigeria": (4.27, 13.89, 2.67, 14.68),
}

# Spellings a person may type, mapped onto a key of COUNTRY_BOXES.
_ALIASES: dict[str, str] = {
    "sg": "Singapore",
    "republic of singapore": "Singapore",
    "id": "Indonesia",
    "republic of indonesia": "Indonesia",
    "my": "Malaysia",
    "th": "Thailand",
    "kingdom of thailand": "Thailand",
    "ph": "Philippines",
    "the philippines": "Philippines",
    "vn": "Vietnam",
    "viet nam": "Vietnam",
    "hk": "Hong Kong",
    "hong kong sar": "Hong Kong",
    "hong kong s.a.r.": "Hong Kong",
    "cn": "China",
    "prc": "China",
    "mainland china": "China",
    "people's republic of china": "China",
    "jp": "Japan",
    "kr": "South Korea",
    "korea": "South Korea",
    "republic of korea": "South Korea",
    "au": "Australia",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "ie": "Ireland",
    "republic of ireland": "Ireland",
    "us": "United States",
    "usa": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "america": "United States",
    "united states of america": "United States",
    "ca": "Canada",
    "tr": "Turkey",
    "turkiye": "Turkey",
    "türkiye": "Turkey",
    "br": "Brazil",
    "brasil": "Brazil",
    "ar": "Argentina",
    "ng": "Nigeria",
}

_LOOKUP: dict[str, str] = {name.lower(): name for name in COUNTRY_BOXES}
_LOOKUP.update(_ALIASES)


def _segments(hint: str) -> list[str]:
    """The hint itself, then its comma or slash separated parts, last part first.

    A hint is usually written from the narrow end to the wide one ("West
    Kalimantan, Indonesia"), so the country is the last part.
    """
    cleaned = " ".join(hint.split())
    if not cleaned:
        return []
    parts = [p.strip(" .") for p in re.split(r"[,/|]", cleaned)]
    return [cleaned, *[p for p in reversed(parts) if p]]


def resolve_country(hint: str) -> str | None:
    """The country a region hint names, or None when the table does not hold it."""
    for segment in _segments(hint):
        country = _LOOKUP.get(segment.lower())
        if country is not None:
            return country
    return None


def in_country(country: str, lat: float, lon: float) -> bool:
    """Whether (lat, lon) falls inside the rectangle drawn around `country`."""
    min_lat, max_lat, min_lon, max_lon = COUNTRY_BOXES[country]
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def region_mismatch(hint: str, lat: float, lon: float) -> str | None:
    """A warning when the coordinate cannot be in the country the hint names.

    Returns None when the hint is empty, when it names a country the table does
    not hold, or when the coordinate falls inside that country's rectangle.
    """
    if not hint.strip():
        return None
    country = resolve_country(hint)
    if country is None or in_country(country, lat, lon):
        return None
    return (
        f"centroid ({lat:.4f}, {lon:.4f}) is outside {country}, which the region "
        f"hint {hint.strip()!r} names: the draft may describe a different place"
    )
