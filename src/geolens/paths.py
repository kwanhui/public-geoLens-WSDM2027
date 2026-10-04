"""On-disk cache locations for onboarded city profiles and city embeddings.

Everything GeoLens writes between runs lives under one root:
`$GEOLENS_CACHE_DIR` when it is set, otherwise `~/.geolens`. Two instances
given different roots do not see each other's onboarded cities, which matters
when a demo instance and an evaluation run share a machine. The root is read on
each call rather than captured at import.
"""

from __future__ import annotations

import os
from pathlib import Path

ONBOARDED_CITIES = "onboarded_cities"
CITY_EMBEDDINGS = "city_embeddings"


def cache_root() -> Path:
    """Root directory for GeoLens caches."""
    override = os.getenv("GEOLENS_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".geolens"


def cache_subdir(name: str, *, create: bool = False) -> Path:
    """A named directory under `cache_root()`, created on request."""
    path = cache_root() / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
