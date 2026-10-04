"""Check a drafted centroid against the places already in the catalogue.

A draft that lands on another catalogue place's centroid is usually the model
resolving an ambiguous name to the wrong place, and the region check will not
catch it: asked for "Canberra" with the hint "Singapore", the drafting model
returns Singapore's own centroid, which is inside Singapore. The radius is
1 km, tight enough not to confuse two genuinely distinct places and loose
enough to identify a centroid copied from a neighbour.
"""

from __future__ import annotations

from geolens.engines._coords import coords_for
from geolens.geo import haversine_km

DUPLICATE_RADIUS_KM = 1.0


def nearest_catalogue_place(
    lat: float, lon: float, catalogue: list[str], *, exclude: str = ""
) -> tuple[str, float] | None:
    """The catalogue place closest to (lat, lon), with its distance in km."""
    skip = exclude.strip().lower()
    best: tuple[str, float] | None = None
    for name in catalogue:
        if name.strip().lower() == skip:
            continue
        other = coords_for(name)
        if other is None:
            continue
        distance = haversine_km((lat, lon), other)
        if best is None or distance < best[1]:
            best = (name, distance)
    return best


def proximity_warning(
    name: str, lat: float | None, lon: float | None, catalogue: list[str]
) -> str | None:
    """A warning when the drafted coordinate sits on an existing place."""
    if lat is None or lon is None:
        return None
    nearest = nearest_catalogue_place(lat, lon, catalogue, exclude=name)
    if nearest is None or nearest[1] > DUPLICATE_RADIUS_KM:
        return None
    place, distance = nearest
    return (
        f"the drafted coordinate is {distance:.1f} km from {place}, which is already "
        "in the catalogue: check that the draft is about the place you asked for and "
        "not about its better-known namesake"
    )
