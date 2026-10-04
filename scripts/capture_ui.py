#!/usr/bin/env python3
"""Capture the GeoLens interface panels the paper prints in Figures 1 and 2.

By default it runs the app locally in placeholder mode and drives it with
Playwright; with `--base-url` it drives an already-running instance, so the
figure shows real predictions. The verification query runs before the
onboarding draft, every onboarded place is removed first and the drafted one is removed
afterwards through the page, so the catalogue is the paper's 50 before and
after. Nothing is written into the page. The panels are trimmed by hiding the
elements listed below, each of which is on screen for every visitor.

Outputs in --out-dir:
    ui-demo.png        the whole result pane of the verification query
    ui-demo-crop.png   map, verification flag and fused prediction
    ui-flag-crop.png   map, legend and the whole verification flag
    ui-flag-short-crop.png  map, legend and the flag down to its headline,
                       caveat and gazetteer note
    ui-batch.png       the whole bulk-evaluation panel
    ui-batch-crop.png  the per-engine and fusion summary
    ui-onboard.png     the whole onboarding panel
    ui-onboard-crop.png  name, region hint, status, warnings, aliases, landmarks

--print-figure writes two files, sized for the paper:
    ui-print-left.png   a short map framed on the two consensus places, with
                        the verification flag under it
    ui-print-right.png  the top of the onboarding form after the draft
                        returns and before the operator edits it

--print-map-panel writes one file, the map as the result pane shows it:
    ui-print-map.png    the map's heading, the map with its zoom control and
                        attribution, and the legend strip under it

--print-figure-cards writes one file, the alternative left panel:
    ui-print-left-cards.png  the verification flag's headline box, then the
                        post-level engine cards of the same query, no map

WHICH TAB EACH CAPTURE IS TAKEN ON. The interface has one tab per function,
and this script opens the one the panel belongs to: Post vs. User
Verification for the query panels, because the query carries a post and a
timeline; Cold-start Place Onboarding for the onboarding panels; Bulk
Evaluation for the bulk panel.

WHAT THE PRINT CAPTURE HIDES. Each panel is bounded by height in the paper.

Left panel:
    #results-heading, #result-source, #result-stage, #mode-banner, #run-line
    #replay-banner, #result-waiting
    #map-heading, #map-keys        the map's heading and its keyboard note
    #map-legend                    the legend strip
    #map-notice, #near-miss-note, #agreement-note
    #map-list-wrap                 the coordinate list under the flag
    .disagreement-why              the expandable "candidate causes" toggle
    #flag-error-rate               the measured error-rate sentence
    .info-btn, .info-body          every circled "i" and the note it opens
    #ensembles, #comparison, #per-engine-section

Left cards panel: everything the left panel hides except
#per-engine-section, plus
    #map                           the map itself
    .section-title                 the "All engines (9)" heading
    #reach-summary                 how many engines reached the place
    #reference-provenance          the benchmark footnote
    .engine-group summary          the "Post-level engines (4)" line
    .engine-group[data-bucket="user"]   the user-level cards
    .topk                          each card's runner-up places
    .evidence-line                 the gazetteer's matched term
The four post-level cards are also laid out in one row, and the space the
hidden heading left behind is closed up, as the map's height is set for the
other left panel.

Map panel: nothing inside the crop except
    .info-btn, .info-body          the legend's circled "i" and its note
The crop runs from the map's heading to the bottom of the legend, so the
run line above it and the flag below it are outside the image rather than
hidden. The map is shortened for print (--map-height, default 170 px) and
refitted on the pair the flag compares, as for the left panel. The query
is the only request this mode makes; it drafts no place.

Right panel:
    #onboard-region-hint           the hint under the region field
    .onboard-source                the "Source: ... Place: ..." line
    .onboard-card                  the five drafted fields and the coordinate
    .onboard-button-row            Save and use / Draft again
    #onboarded-places              what else is onboarded on this instance
    .info-btn, .info-body          every circled "i" and the note it opens

Usage:
    python3 scripts/capture_ui.py --out-dir figures
    python3 scripts/capture_ui.py --print-figure --base-url https://kwanhui-geo-lens.hf.space \
        --out-dir figures
    python3 scripts/capture_ui.py --print-map-panel --base-url https://kwanhui-geo-lens.hf.space \
        --viewport-width 608 --out-dir figures
    python3 scripts/capture_ui.py --print-figure-cards --base-url https://kwanhui-geo-lens.hf.space \
        --out-dir figures
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from geolens.onboarding.catalogue import is_default_city

STATIC = Path(__file__).resolve().parents[1] / "src" / "geolens" / "ui" / "static"
EXAMPLE_CSV = STATIC / "example_test_set.csv"
SCENARIOS = STATIC / "scenarios"

# The regions each cropped figure has to contain, as page selectors. The crop
# is the union of their bounding boxes plus a margin, so a layout change moves
# the crop instead of cutting content out of it.
CROPS = {
    "ui-demo-crop.png": ["#map", "#disagreement-banner", "#near-miss-note", "#ensembles"],
    "ui-flag-crop.png": ["#map", "#map-legend", "#disagreement-banner"],
    # A shorter panel for print: the flag's first paragraph and the note under
    # it, and none of the expandable causes or the error-rate sentence.
    "ui-flag-short-crop.png": [
        "#map", "#map-legend", ".disagreement-headline", ".flag-caveat",
        "#flag-gazetteer-note",
    ],
    "ui-batch-crop.png": ["#batch-summary"],
    "ui-onboard-crop.png": [
        "#onboard_city",
        "#onboard_region",
        "#onboard-status",
        "#onboard-warnings",
        '.onboard-card[data-field="aliases"]',
        '.onboard-card[data-field="landmarks"]',
    ],
    # The two panels the paper prints. Their contents are trimmed by hiding
    # the elements listed in this module's docstring, never by adding
    # anything to the page.
    "ui-print-left.png": ["#map", "#disagreement-banner"],
    # The alternative left panel: the flag's headline box over the
    # post-level engine cards of the same query, with no map.
    "ui-print-left-cards.png": [
        "#disagreement-banner", '.engine-cards[data-bucket="post"]',
    ],
    # The map as a visitor sees it in the result pane: its heading, the map
    # and the legend strip.
    "ui-print-map.png": ["#map-heading", "#map", "#map-legend"],
    "ui-print-right.png": [
        ".onboard-input-row",
        "#onboard_region_label",
        "#onboard_region",
        "#onboard-status",
        "#onboard-warnings",
    ],
}
CROP_MARGIN_PX = 10
# A tighter margin for the print panels, which are measured against a budget.
PRINT_CROP_MARGIN_PX = 6

# What the print capture hides, per panel. Listed in the docstring above.
PRINT_HIDDEN = {
    "left": [
        "#results-heading", "#result-source", "#result-stage",
        "#mode-banner", "#run-line",
        "#replay-banner", "#result-waiting",
        "#map-heading", "#map-keys", "#map-legend", "#map-notice",
        "#near-miss-note", "#agreement-note", "#map-list-wrap",
        ".disagreement-why", "#flag-error-rate",
        ".info-btn", ".info-body",
        "#ensembles", "#comparison", "#per-engine-section",
    ],
    "left-cards": [
        "#results-heading", "#result-source", "#result-stage",
        "#mode-banner", "#run-line",
        "#replay-banner", "#result-waiting",
        "#map-heading", "#map-keys", "#map", "#map-legend", "#map-notice",
        "#near-miss-note", "#agreement-note", "#map-list-wrap",
        ".disagreement-why", "#flag-error-rate",
        ".info-btn", ".info-body",
        "#ensembles", "#comparison",
        ".section-title", "#reach-summary", "#reference-provenance",
        ".engine-group summary", '.engine-group[data-bucket="user"]',
        ".topk", ".evidence-line",
    ],
    "map-panel": [".info-btn", ".info-body"],
    "right": [
        "#onboard-region-hint", ".onboard-source", ".onboard-card",
        ".onboard-button-row", "#onboarded-places",
        ".info-btn", ".info-body",
    ],
}

# The left panel's height budget, and how short the map may be made to meet
# it. A restyled flag that runs to another line takes the space off the map.
PRINT_LEFT_MAX_HEIGHT_PX = 370
PRINT_MAP_HEIGHT_PX = 150
PRINT_MAP_MIN_HEIGHT_PX = 120
PRINT_RIGHT_MAX_HEIGHT_PX = 420
# The map panel's default map height. At a 608 px viewport the crop is 588 px
# wide, so a full-column figure (241 pt) prints the 13.5 px legend at 5.5 pt,
# and a 170 px map keeps the panel at 279 px, under half its width.
PRINT_MAP_PANEL_HEIGHT_PX = 170
# The cards panel fills the same column, so it shares the left panel's width
# and its height budget.
PRINT_LEFT_CARDS_MAX_WIDTH_PX = 824
# Measured at 824 px on the hosted instance's layout, where every badge reads
# "real": 381 px. A placeholder-mode capture is 409, because "placeholder"
# wraps the card's head onto a second row, and the paper's figure is captured
# against the hosted instance. 400 is the smallest multiple of ten that holds
# the 381 with about one line of the small type to spare.
PRINT_LEFT_CARDS_MAX_HEIGHT_PX = 400


def _load_scenario(scenario_id: str) -> dict:
    """The shipped scenario file, so the figure onboards what the demo onboards."""
    path = SCENARIOS / f"{scenario_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _require_pillow():
    """Pillow does the trimming. Say so plainly rather than skipping it."""
    try:
        from PIL import Image, ImageChops
    except ImportError as e:
        raise SystemExit(
            "Pillow is missing, and the captured panels cannot be trimmed without it. "
            'Install the dev extra with `pip install -e ".[dev]"`, or `pip install pillow`.'
        ) from e
    return Image, ImageChops


def _trim_to_content(path: Path, pillow, margin: int = 10) -> None:
    """Crop the flat background off a panel screenshot.

    The right-hand pane sits in a grid row whose height is set by the taller
    left column, so a screenshot of the pane carries a band of page background
    below the last card. Everything outside the bounding box of the pixels that
    differ from the corner colour is that band.
    """
    image_mod, chops_mod = pillow
    with image_mod.open(path) as im:
        rgb = im.convert("RGB")
        background = rgb.getpixel((rgb.width - 1, rgb.height - 1))
        flat = image_mod.new("RGB", rgb.size, background)
        box = chops_mod.difference(rgb, flat).getbbox()
        if box is None:
            return  # the whole panel is one colour; nothing to crop to
        left, top, right, bottom = box
        cropped = im.crop((
            max(0, left - margin),
            max(0, top - margin),
            min(rgb.width, right + margin),
            min(rgb.height, bottom + margin),
        ))
        cropped.save(path)


def _crop_box(page, name: str, margin: int = CROP_MARGIN_PX) -> dict | None:
    """The union of the crop's selectors, plus a margin, in CSS pixels."""
    boxes = []
    for selector in CROPS[name]:
        locator = page.locator(selector)
        if locator.count() == 0 or not locator.first.is_visible():
            continue
        box = locator.first.bounding_box()
        if box:
            boxes.append(box)
    if not boxes:
        return None
    left = max(0.0, min(b["x"] for b in boxes) - margin)
    top = max(0.0, min(b["y"] for b in boxes) - margin)
    right = max(b["x"] + b["width"] for b in boxes) + margin
    bottom = max(b["y"] + b["height"] for b in boxes) + margin
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _capture_crop(page, name: str, out: Path, margin: int = CROP_MARGIN_PX) -> bool:
    """Screenshot the union of the crop's selectors. False when none is visible."""
    clip = _crop_box(page, name, margin)
    if clip is None:
        return False
    page.screenshot(path=str(out / name), clip=clip)
    return True


def _hide(page, selectors: list[str]) -> None:
    """Take the listed elements off the capture without touching their text."""
    rules = "\n".join(f"{s} {{ display: none !important; }}" for s in selectors)
    page.add_style_tag(content=rules)


def _set_cards_in_one_row(page) -> None:
    """Lay the post-level cards out in a single row.

    The grid fits as many 230 px columns as the pane allows, which is three
    at the figure's width. The paper's panel is one row of four.
    """
    page.add_style_tag(content=(
        '.engine-cards[data-bucket="post"] '
        "{ grid-template-columns: repeat(4, minmax(0, 1fr)) !important; }\n"
        "#per-engine-section, .engine-group { margin-top: 0 !important; }"
    ))


def _set_map_height(page, height: int) -> None:
    page.add_style_tag(content=f"#map {{ height: {height}px !important; }}")
    # The map has to be told its box changed, and the view refitted on the
    # pair the flag compares, or the arc runs off the shorter panel.
    page.evaluate(
        """() => {
          if (typeof map === 'undefined' || !map) return;
          map.invalidateSize();
          const d = window._lastResult;
          if (d) renderMap(d.ensembles || {}, d.triangulation, d.per_engine || {});
        }"""
    )
    page.wait_for_timeout(400)


def _call(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode("utf-8", "replace")}


def clear_onboarded(base: str) -> list[str]:
    """Remove every onboarded place from the target instance.

    The bulk panel prints the live catalogue size, and a place left behind by
    an earlier visitor put N=52 in the figure against the paper's 50.
    """
    status = _call(base, "GET", "/onboard/status")
    names = [p["name"] for p in status.get("onboarded", [])]
    tokens = _known_tokens()
    removed = []
    for name in names:
        if is_default_city(name):
            continue
        payload = {"city": name}
        if tokens.get(name):
            payload["edit_token"] = tokens[name]
        reply = _call(base, "DELETE", "/onboard", payload)
        if isinstance(reply, dict) and reply.get("error"):
            raise SystemExit(
                f"{name!r} could not be removed from {base} (it was onboarded from elsewhere); "
                "wait for it to expire or use the operator token"
            )
        removed.append(name)
    return removed


def _known_tokens() -> dict[str, str]:
    """Edit tokens that scripts/check_scenarios.py kept from its last run."""
    path = Path.home() / ".geolens" / "scenario-check-tokens.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _wait_healthz(base: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("app did not become healthy in time")


def _type_operator_edits(page, scenario: dict) -> None:
    """Fill the fields the scenario's operator corrects, without saving.

    The figure is about the review step, so it has to show the draft's
    warnings next to what the operator is typing, before the profile is
    written back.
    """
    edits = scenario.get("operator_edits") or {}
    for field in ("aliases", "landmarks", "foods", "slang"):
        values = edits.get(field)
        if isinstance(values, list) and values:
            page.fill(f'textarea[data-field="{field}"]', ", ".join(values))
    if edits.get("lat") is not None:
        page.fill("#onboard_lat", str(edits["lat"]))
    if edits.get("lon") is not None:
        page.fill("#onboard_lon", str(edits["lon"]))


def _reset_through_the_page(page, base: str, city: str) -> None:
    """Remove the place this capture drafted, using the page's own edit token.

    Where the deployment requires an edit token, only the browser that
    drafted the place holds one, so the removal goes through the page rather
    than through a bare DELETE from this script.
    """
    page.evaluate("(name) => resetOnboarded(name)", city)
    page.wait_for_timeout(500)
    left = [p["name"] for p in _call(base, "GET", "/onboard/status").get("onboarded", [])]
    if city in left:
        raise SystemExit(
            f"{city!r} is still onboarded on {base} after the capture. Remove it with "
            "the operator token before running again."
        )
    clear_onboarded(base)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--base-url", default=None,
                    help="drive an already-running instance at this URL instead of "
                         "launching a local placeholder-mode server")
    ap.add_argument("--scenario", default="estate-management",
                    help="scenario file the place and region hint default to")
    ap.add_argument("--city", default=None,
                    help="place name typed into the onboarding form; must not be a catalogue place")
    ap.add_argument("--region", default=None,
                    help="country or region hint typed into the onboarding form")
    ap.add_argument("--two-panel", action="store_true",
                    help="write only the verification-query and onboarding crops, at a larger "
                         "scale; the three-panel figure is illegible at print size")
    ap.add_argument("--print-figure", action="store_true",
                    help="write only ui-print-left.png and ui-print-right.png, the two "
                         "panels sized for the paper: a short map over the flag's first "
                         "paragraph, and the top of the onboarding form. The elements "
                         "hidden for these captures are listed in this module's docstring")
    ap.add_argument("--print-figure-cards", action="store_true",
                    help="write only ui-print-left-cards.png: the verification flag's "
                         "headline box over the post-level engine cards of the same "
                         "query, with no map. The elements hidden for this capture are "
                         "listed in this module's docstring")
    ap.add_argument("--print-map-panel", action="store_true",
                    help="write only ui-print-map.png: the map's heading, the map and its "
                         "legend strip, as the result pane shows them after the viral-post "
                         "query. The elements hidden for this capture are listed in this "
                         "module's docstring")
    ap.add_argument("--viewport-width", type=int, default=None,
                    help="browser width in CSS pixels (default 1280, or 1800 with --two-panel: a "
                         "wider result pane wraps the flag into fewer lines, so the panel is "
                         "flatter and prints larger at a given figure height)")
    ap.add_argument("--map-height", type=int, default=None,
                    help="map height in CSS pixels for the capture (default: the interface's "
                         "own, or 150 with --print-figure, shortened towards 120 when the "
                         "flag needs the space, or 170 with --print-map-panel)")
    ap.add_argument("--scale", type=float, default=None,
                    help="device scale factor (default 2, or 3 with --two-panel and "
                         "the print modes)")
    args = ap.parse_args(argv)

    modes = [args.two_panel, args.print_figure, args.print_figure_cards,
             args.print_map_panel]
    if sum(bool(m) for m in modes) > 1:
        print("--two-panel, --print-figure, --print-figure-cards and --print-map-panel "
              "write different files; pick one.", file=sys.stderr)
        return 2
    print_mode = args.print_figure or args.print_figure_cards or args.print_map_panel

    scenario = _load_scenario(args.scenario)
    args.city = args.city or scenario.get("onboard_city") or "Bidadari Estate"
    args.region = args.region if args.region is not None else scenario.get("region", "")
    scale = args.scale or (3 if (args.two_panel or print_mode) else 2)

    pillow = _require_pillow()

    if is_default_city(args.city):
        print(f"{args.city} is already a catalogue place, so the onboarding panel "
              f"would not show a cold start. Pick a place outside engines/_cities.py.",
              file=sys.stderr)
        return 2

    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    proc = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        port = args.port or _free_port()
        base = f"http://127.0.0.1:{port}"
        env = dict(
            os.environ,
            GEOLENS_STUB_MODE="1",
            MAX_QUERIES_PER_HOUR="0",
            MAX_BATCHES_PER_HOUR="0",
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "geolens.app", "--host", "127.0.0.1", "--port", str(port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    written: list[str] = []
    try:
        _wait_healthz(base, timeout=180.0)

        # Whatever earlier visitors onboarded is still in the shared catalogue
        # and would show up in the bulk panel's candidate count.
        removed = clear_onboarded(base)
        if removed:
            print(f"removed {len(removed)} onboarded place(s) first: {', '.join(removed)}")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": args.viewport_width or (1800 if args.two_panel else 1280),
                          "height": 2200},
                device_scale_factor=scale
            )
            page.goto(base, wait_until="networkidle")

            map_height = args.map_height
            if map_height:
                page.add_style_tag(content=f"#map {{ height: {map_height}px !important; }}")
                page.evaluate("() => { if (typeof map !== 'undefined' && map) map.invalidateSize(); }")

            # --- The query panel. It runs before anything is drafted, so
            # no drafted pin sits on the map behind the result and the query
            # answers from the 50 built-in places. The query carries a post
            # and a timeline, so it is the verification tab that shows both
            # boxes and sends both. ---
            page.click("#tab-verify")
            page.fill("#post", "Massive fire at Marina Bay Sands! #Singapore #breaking")
            page.fill("#user_posts",
                      "Best ramen in Shibuya tonight\nShinjuku always crowded\nGot sake at Asakusa")
            page.click("#geolocate-btn")
            page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
            page.wait_for_timeout(800)
            whole_panels = not (args.two_panel or print_mode)

            if args.print_figure:
                # --- The paper's left panel. Everything hidden here is listed
                # in the module docstring and is on screen for a visitor. ---
                _hide(page, PRINT_HIDDEN["left"])
                height = args.map_height or PRINT_MAP_HEIGHT_PX
                while True:
                    _set_map_height(page, height)
                    box = _crop_box(page, "ui-print-left.png", PRINT_CROP_MARGIN_PX)
                    if box is None:
                        print("the verification-query panel is not on screen", file=sys.stderr)
                        return 4
                    if (box["height"] <= PRINT_LEFT_MAX_HEIGHT_PX
                            or height <= PRINT_MAP_MIN_HEIGHT_PX
                            or args.map_height):
                        break
                    height = max(PRINT_MAP_MIN_HEIGHT_PX, height - 10)
                if _capture_crop(page, "ui-print-left.png", out, PRINT_CROP_MARGIN_PX):
                    written.append("ui-print-left.png")
                    print(f"ui-print-left.png: {box['width']:.0f} by {box['height']:.0f} "
                          f"CSS pixels, map {height} px tall, at scale {scale:g}")
                if box["height"] > PRINT_LEFT_MAX_HEIGHT_PX:
                    print(f"the left panel is {box['height']:.0f} CSS pixels tall, over the "
                          f"{PRINT_LEFT_MAX_HEIGHT_PX} budget, with the map already at its "
                          f"{PRINT_MAP_MIN_HEIGHT_PX} px floor.", file=sys.stderr)
            elif args.print_map_panel:
                # --- The map as the result pane shows it: heading, map and
                # legend. Only the legend's "i" is hidden, as listed in the
                # module docstring. ---
                _hide(page, PRINT_HIDDEN["map-panel"])
                _set_map_height(page, args.map_height or PRINT_MAP_PANEL_HEIGHT_PX)
                # The query scrolls the page to the flag, which leaves the map
                # above the viewport; the crop is taken in viewport coordinates.
                page.eval_on_selector("#map-heading",
                                      "el => el.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(300)
                box = _crop_box(page, "ui-print-map.png", PRINT_CROP_MARGIN_PX)
                if box is None:
                    print("the map panel is not on screen", file=sys.stderr)
                    return 4
                if _capture_crop(page, "ui-print-map.png", out, PRINT_CROP_MARGIN_PX):
                    written.append("ui-print-map.png")
                    print(f"ui-print-map.png: {box['width']:.0f} by {box['height']:.0f} "
                          f"CSS pixels, at scale {scale:g}")
                browser.close()
                print(f"wrote {', '.join(written)} to {out}")
                return 0
            elif args.print_figure_cards:
                # --- The alternative left panel: the flag's headline box over
                # the post-level engine cards of the same query. Everything
                # hidden here is listed in the module docstring. ---
                _hide(page, PRINT_HIDDEN["left-cards"])
                _set_cards_in_one_row(page)
                page.eval_on_selector(
                    '.engine-group[data-bucket="post"]', "el => el.open = true")
                page.wait_for_timeout(300)
                box = _crop_box(page, "ui-print-left-cards.png", PRINT_CROP_MARGIN_PX)
                if box is None:
                    print("the flag and the post-level cards are not on screen",
                          file=sys.stderr)
                    return 4
                if _capture_crop(page, "ui-print-left-cards.png", out, PRINT_CROP_MARGIN_PX):
                    written.append("ui-print-left-cards.png")
                    print(f"ui-print-left-cards.png: {box['width']:.0f} by "
                          f"{box['height']:.0f} CSS pixels, at scale {scale:g}")
                over = [
                    f"{box['width']:.0f} wide against {PRINT_LEFT_CARDS_MAX_WIDTH_PX}"
                    if box["width"] > PRINT_LEFT_CARDS_MAX_WIDTH_PX else "",
                    f"{box['height']:.0f} tall against {PRINT_LEFT_CARDS_MAX_HEIGHT_PX}"
                    if box["height"] > PRINT_LEFT_CARDS_MAX_HEIGHT_PX else "",
                ]
                if any(over):
                    print("the cards panel is over its budget: "
                          + ", ".join(x for x in over if x), file=sys.stderr)
            else:
                # The long per-engine list is illegible at figure size, so it
                # is hidden for the screenshot.
                page.eval_on_selector("#per-engine-section", "el => el.style.display='none'")
                if whole_panels:
                    demo_png = out / "ui-demo.png"
                    page.locator("section.right-pane").screenshot(path=str(demo_png))
                    _trim_to_content(demo_png, pillow)
                    written.append("ui-demo.png")
                for crop in ("ui-demo-crop.png", "ui-flag-crop.png",
                             "ui-flag-short-crop.png"):
                    if _capture_crop(page, crop, out):
                        written.append(crop)
                page.eval_on_selector("#per-engine-section", "el => el.style.display=''")

            # --- Onboarding panel: the draft with the operator's aliases and
            # landmarks typed in and the warnings still showing, which is the
            # review step the scenario describes. Nothing is saved, so the
            # catalogue is left as it was found. With --print-figure the draft
            # is captured before any edit, which is what the paper's right
            # panel shows. The form has its own tab. ---
            page.click("#tab-onboard")
            page.fill("#onboard_city", args.city)
            page.fill("#onboard_region", args.region)
            page.click("#onboard-btn")
            page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
            try:
                page.wait_for_selector("#onboard-warnings:not(.hidden)", timeout=10000)
            except Exception:
                print(
                    f"the draft for {args.city!r} came back with no validation warning, so the "
                    "onboarding figure would not show the review step. Pick a place whose draft "
                    "is incomplete, or capture in placeholder mode (drop --base-url).",
                    file=sys.stderr,
                )
                return 3
            if args.print_figure_cards:
                pass  # the cards panel is the whole output of this mode
            elif args.print_figure:
                _hide(page, PRINT_HIDDEN["right"])
                page.wait_for_timeout(200)
                box = _crop_box(page, "ui-print-right.png", PRINT_CROP_MARGIN_PX)
                if box and _capture_crop(page, "ui-print-right.png", out,
                                         PRINT_CROP_MARGIN_PX):
                    written.append("ui-print-right.png")
                    print(f"ui-print-right.png: {box['width']:.0f} by {box['height']:.0f} "
                          f"CSS pixels, at scale {scale:g}")
                    if box["height"] > PRINT_RIGHT_MAX_HEIGHT_PX:
                        print(f"the right panel is {box['height']:.0f} CSS pixels tall, over "
                              f"the {PRINT_RIGHT_MAX_HEIGHT_PX} budget.", file=sys.stderr)
            else:
                _type_operator_edits(page, scenario)
                page.wait_for_timeout(200)
                if whole_panels:
                    page.locator("#onboard-panel").screenshot(path=str(out / "ui-onboard.png"))
                    written.append("ui-onboard.png")
                if _capture_crop(page, "ui-onboard-crop.png", out):
                    written.append("ui-onboard-crop.png")
            # The draft put the place in the catalogue. Take it back out
            # through the page, which holds the edit token the draft returned,
            # so an instance that requires one is left as it was found.
            _reset_through_the_page(page, base, args.city)

            if whole_panels:
                # --- Bulk panel ---
                page.click('button[data-mode="batch"]')
                page.set_input_files("#batch-file", str(EXAMPLE_CSV))
                page.click("#batch-run-btn")
                page.wait_for_selector("#batch-summary:not(.hidden)", timeout=600000)
                page.wait_for_timeout(500)
                page.eval_on_selector("#batch-rows", "el => el.style.display='none'")
                page.eval_on_selector(".batch-intro", "el => el.style.display='none'")
                page.locator("#batch-panel").screenshot(path=str(out / "ui-batch.png"))
                written.append("ui-batch.png")
                if _capture_crop(page, "ui-batch-crop.png", out):
                    written.append("ui-batch-crop.png")

            browser.close()
        print(f"wrote {', '.join(written)} to {out}")
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
