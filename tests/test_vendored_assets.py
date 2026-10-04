from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/geolens/ui/static"
VENDOR = STATIC / "vendor/leaflet"


def test_the_vendored_copy_is_complete() -> None:
    for name in ("leaflet.js", "leaflet.css", "LICENSE", "VERSION"):
        assert (VENDOR / name).is_file(), name
    for image in (
        "marker-icon.png",
        "marker-icon-2x.png",
        "marker-shadow.png",
        "layers.png",
        "layers-2x.png",
    ):
        assert (VENDOR / "images" / image).is_file(), image


def test_the_page_loads_the_vendored_copy() -> None:
    index = (STATIC / "index.html").read_text()
    assert 'src="/static/vendor/leaflet/leaflet.js"' in index
    assert 'href="/static/vendor/leaflet/leaflet.css"' in index
    assert "unpkg.com" not in index
