"""The map's coordinate table and the server's hold the same cities.

`engines/_coords.py` turns a predicted label into a distance error and
`app.js`'s `CITY_COORDS` places the marker. The two were written separately
and have drifted before.
"""

from __future__ import annotations

import re
from pathlib import Path

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._coords import CITY_COORDS

APP_JS = Path(__file__).resolve().parents[1] / "src/geolens/ui/static/app.js"


def _map_coords() -> dict[str, tuple[float, float]]:
    text = APP_JS.read_text()
    start = text.index("const CITY_COORDS = {")
    block = text[start : text.index("};", start)]
    return {
        name: (float(lat), float(lon))
        for name, lat, lon in re.findall(
            r'"([^"]+)":\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]', block
        )
    }


def test_the_two_tables_hold_the_same_cities() -> None:
    assert set(_map_coords()) == set(CITY_COORDS)


def test_the_two_tables_agree_on_every_coordinate() -> None:
    js = _map_coords()
    for city, (lat, lon) in CITY_COORDS.items():
        assert js[city] == (lat, lon), city


def test_every_catalogue_city_can_be_pinned() -> None:
    missing = [c for c in DEFAULT_CITIES if c not in _map_coords()]
    assert not missing, f"no map coordinate for {missing}"
