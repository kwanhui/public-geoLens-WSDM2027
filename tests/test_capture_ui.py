"""The parts of scripts/capture_ui.py that run without a browser."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from geolens.engines._cities import DEFAULT_CITIES
from geolens.ui.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/geolens/ui/static"


def _load_script():
    spec = importlib.util.spec_from_file_location("capture_ui", ROOT / "scripts/capture_ui.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture_ui = _load_script()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_clearing_onboarded_places_restores_the_built_in_catalogue(client, monkeypatch) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    client.post("/onboard", json={"city": "Bidadari Estate", "region": "Singapore"})
    assert client.get("/onboard/status").json()["catalogue_size"] == len(DEFAULT_CITIES) + 2

    def _call(base, method, path, payload=None):
        return client.request(method, path, json=payload).json()

    monkeypatch.setattr(capture_ui, "_call", _call)
    removed = capture_ui.clear_onboarded("http://testserver")

    assert sorted(removed) == ["Bidadari Estate", "Sintang"]
    assert client.get("/onboard/status").json()["catalogue_size"] == len(DEFAULT_CITIES)


def test_the_print_modes_raise_the_scale() -> None:
    source = (ROOT / "scripts/capture_ui.py").read_text()
    assert '"--two-panel"' in source
    assert "3 if (args.two_panel or print_mode) else 2" in source


def test_every_crop_names_the_regions_it_must_contain() -> None:
    assert set(capture_ui.CROPS) == {
        "ui-demo-crop.png", "ui-flag-crop.png", "ui-flag-short-crop.png",
        "ui-batch-crop.png", "ui-onboard-crop.png",
        "ui-print-left.png", "ui-print-left-cards.png", "ui-print-right.png",
        "ui-print-map.png",
    }
    html = (STATIC / "index.html").read_text()
    for selectors in capture_ui.CROPS.values():
        for selector in selectors:
            token = selector.split("[")[0].lstrip("#.")
            assert token in html, f"{selector} matches nothing in the interface"


def test_the_onboarding_capture_types_the_operators_edits_before_saving() -> None:
    source = (ROOT / "scripts/capture_ui.py").read_text()
    assert "def _type_operator_edits(" in source
    assert "_type_operator_edits(page, scenario)" in source
    # It must happen after the warnings are on screen and before any save.
    typed = source.index("_type_operator_edits(page, scenario)")
    warned = source.index('page.wait_for_selector("#onboard-warnings:not(.hidden)"')
    assert warned < typed
    assert "#onboard-save-btn" not in source


def test_the_short_flag_crop_stops_at_the_headline_and_its_note() -> None:
    """A shorter left panel for print: no expandable toggle, no error rate."""
    selectors = capture_ui.CROPS["ui-flag-short-crop.png"]
    assert selectors == ["#map", "#map-legend", ".disagreement-headline",
                         ".flag-caveat", "#flag-gazetteer-note"]
    assert "#disagreement-banner" not in selectors  # the whole box is the long crop
    assert "#flag-error-rate" not in selectors


def test_the_long_flag_crop_carries_the_legend_between_map_and_flag() -> None:
    assert capture_ui.CROPS["ui-flag-crop.png"] == [
        "#map", "#map-legend", "#disagreement-banner",
    ]


def test_the_query_runs_before_the_onboarding_draft() -> None:
    """A draft left a pin on the map behind the single-query figure."""
    source = (ROOT / "scripts/capture_ui.py").read_text()
    queried = source.index('page.click("#geolocate-btn")')
    drafted = source.index('page.click("#onboard-btn")')
    assert queried < drafted


def test_the_print_figure_mode_writes_two_panels_at_scale_three() -> None:
    source = (ROOT / "scripts/capture_ui.py").read_text()
    assert '"--print-figure"' in source
    assert "3 if (args.two_panel or print_mode) else 2" in source
    assert capture_ui.CROPS["ui-print-left.png"] == ["#map", "#disagreement-banner"]
    # The left panel is the map over the flag's first paragraph and its note,
    # with nothing between them.
    assert capture_ui.PRINT_MAP_HEIGHT_PX == 150
    assert capture_ui.PRINT_MAP_MIN_HEIGHT_PX == 120
    assert capture_ui.PRINT_LEFT_MAX_HEIGHT_PX == 370
    assert capture_ui.PRINT_RIGHT_MAX_HEIGHT_PX == 420


def test_every_print_panel_hides_the_info_notes() -> None:
    """The circled "i" and its note are interface, not figure content."""
    for panel, selectors in capture_ui.PRINT_HIDDEN.items():
        assert ".info-btn" in selectors, panel
        assert ".info-body" in selectors, panel


def test_the_print_capture_hides_and_never_injects() -> None:
    """Hiding is allowed; adding text the interface does not show is not."""
    source = (ROOT / "scripts/capture_ui.py").read_text()
    body = source.split('"""', 2)[2]
    # Nothing is written into the page: no innerHTML, no textContent, no
    # inserted node anywhere in the script.
    for forbidden in ("innerHTML", "textContent", "insertAdjacent", "createElement"):
        assert forbidden not in body, forbidden
    hide = source.split("def _hide(", 1)[1].split("\n\n", 1)[0]
    assert "display: none !important" in hide


def test_the_docstring_lists_every_element_the_print_capture_hides() -> None:
    source = (ROOT / "scripts/capture_ui.py").read_text()
    docstring = source.split('"""', 2)[1]
    assert "WHAT THE PRINT CAPTURE HIDES" in docstring
    for panel, selectors in capture_ui.PRINT_HIDDEN.items():
        for selector in selectors:
            assert selector in docstring, f"{selector} ({panel}) is not in the docstring"
    markup = (STATIC / "index.html").read_text() + (STATIC / "app.js").read_text()
    for selectors in capture_ui.PRINT_HIDDEN.values():
        for selector in selectors:
            # A descendant selector matches only if each of its parts does,
            # and a card's own parts are written by app.js.
            for part in selector.split():
                assert part.split("[")[0].lstrip("#.") in markup, selector


def test_the_print_right_panel_stops_at_the_warnings_box() -> None:
    """Name, region hint, status line, warnings box, and nothing below."""
    assert capture_ui.CROPS["ui-print-right.png"] == [
        ".onboard-input-row", "#onboard_region_label", "#onboard_region",
        "#onboard-status", "#onboard-warnings",
    ]
    hidden = capture_ui.PRINT_HIDDEN["right"]
    assert ".onboard-card" in hidden          # every drafted field
    assert ".onboard-button-row" in hidden    # Save and use / Draft again
    assert "#onboard-region-hint" in hidden   # the long helper paragraph
    # The draft is captured before the operator types anything.
    source = (ROOT / "scripts/capture_ui.py").read_text()
    print_branch = source.split("if args.print_figure:\n                _hide(page, "
                                'PRINT_HIDDEN["right"])', 1)[1].split("else:", 1)[0]
    assert "_type_operator_edits" not in print_branch


def test_the_bulk_view_labels_the_bundled_example_on_screen() -> None:
    js = (STATIC / "app.js").read_text()
    assert "Authored example, 50 rows" in js
    # The long form moved behind the note's icon, and is still word for word.
    assert "not the WNUT-2016 evaluation" in js
    assert 'id="batch-file-note"' in (STATIC / "index.html").read_text()
    # The note the label opens is built from the same helper as the label.
    assert 'infoNote("i-file-note"' in js


def test_the_cards_panel_is_the_flag_over_the_post_level_cards() -> None:
    assert capture_ui.CROPS["ui-print-left-cards.png"] == [
        "#disagreement-banner", '.engine-cards[data-bucket="post"]',
    ]
    hidden = capture_ui.PRINT_HIDDEN["left-cards"]
    assert "#map" in hidden                                  # no map in this panel
    assert '.engine-group[data-bucket="user"]' in hidden      # post level only
    assert "#per-engine-section" not in hidden                # the cards stay
    assert capture_ui.PRINT_LEFT_CARDS_MAX_WIDTH_PX == 824
    # The hosted layout measures 381 px tall; a placeholder-mode capture is
    # 409, because "placeholder" wraps each card's head onto a second row.
    assert capture_ui.PRINT_LEFT_CARDS_MAX_HEIGHT_PX == 400
    source = (ROOT / "scripts/capture_ui.py").read_text()
    assert '"--print-figure-cards"' in source


def test_the_drafted_place_is_removed_through_the_page() -> None:
    """The edit token the draft returned lives in the page, not in this script."""
    source = (ROOT / "scripts/capture_ui.py").read_text()
    assert "def _reset_through_the_page(" in source
    assert '_reset_through_the_page(page, base, args.city)' in source
    assert 'page.evaluate("(name) => resetOnboarded(name)", city)' in source
