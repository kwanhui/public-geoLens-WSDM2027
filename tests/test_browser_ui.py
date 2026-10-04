"""The interface, driven in a browser with Playwright.

Covers what a file-reading test cannot see: map controls exposed to assistive
technology, focus placement, no sideways scroll at 320 CSS pixels, the
consensus line drawn the short way round the world, and each export standing
alone. The instance runs on a port in 7890 to 7899 with its own cache
directory, so a developer's own server on 7860 is untouched. The whole module
is skipped when Playwright or its browser is not installed.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path(__file__).resolve().parent / "_ui_instance.py"
EXAMPLE_CSV = ROOT / "src/geolens/ui/static/example_test_set.csv"

PORT_RANGE = range(7890, 7900)

# Drawn as one Mercator segment, the Tokyo to San Francisco line spans 262
# degrees of longitude westward rather than 98 over the Pacific.
TOKYO = [35.6762, 139.6503]
SAN_FRANCISCO = [37.7749, -122.4194]
SINGAPORE = [1.3521, 103.8198]


def _free_port() -> int:
    for port in PORT_RANGE:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {PORT_RANGE.start}..{PORT_RANGE.stop - 1}")


def _wait_healthy(base: str, proc: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the instance exited with {proc.returncode}")
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    raise RuntimeError("the instance did not become healthy in time")


def _start_instance_with(tmp_path: Path, extra: dict[str, str]):
    """An instance with one or two settings changed, on its own port and cache."""
    return _start_instance(tmp_path, extra=extra)


def _start_instance(tmp_path: Path, *, toy_engine: bool = False,
                    extra: dict[str, str] | None = None):
    port = _free_port()
    env = dict(
        os.environ,
        GEOLENS_STUB_MODE="1",
        GEOLENS_CACHE_DIR=str(tmp_path / "cache"),
        MAX_QUERIES_PER_HOUR="0",
        MAX_BATCHES_PER_HOUR="0",
        MAX_PROFILES_PER_HOUR="0",
        PYTHONPATH=str(ROOT / "src"),
    )
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    if toy_engine:
        env["GEOLENS_TEST_TOY_ENGINE"] = "1"
    env.update(extra or {})
    proc = subprocess.Popen(
        [sys.executable, str(LAUNCHER), str(port)],
        env=env, cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(base, proc)
    except Exception:
        proc.kill()
        raise
    return base, proc


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def instance(tmp_path_factory):
    base, proc = _start_instance(tmp_path_factory.mktemp("instance"))
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def page(browser, instance):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                              accept_downloads=True)
    p = ctx.new_page()
    p.goto(instance, wait_until="networkidle")
    yield p
    ctx.close()


OSINT_POST = "Massive fire at Marina Bay Sands! #Singapore #breaking"
OSINT_TIMELINE = "Best ramen in Shibuya tonight\nShinjuku always crowded\nGot sake at Asakusa"


def run_query(page, post: str = OSINT_POST, timeline: str = OSINT_TIMELINE) -> None:
    # Both boxes are on screen, and both are sent, on the verification tab.
    page.click("#tab-verify")
    page.fill("#post", post)
    page.fill("#user_posts", timeline)
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    page.wait_for_timeout(400)


# ----- the roster comes from the server ---------------------------------------

def test_an_engine_the_server_registers_reaches_the_page(browser, tmp_path):
    """The roster is read from GET /instance, so a new engine needs no edit to the page."""
    base, proc = _start_instance(tmp_path, toy_engine=True)
    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(base, wait_until="networkidle")
        run_query(page, post="Fire at the Bedok hawker centre", timeline="")

        cards = page.locator("#per-engine-section .engine-name").all_inner_texts()
        assert "Toy nearest" in cards, cards
        # The counts follow the roster, not a number written into the page.
        assert page.inner_text("#engine-count") == "10"
        assert page.inner_text("#tagline-engines") == "10"
        # And so does the local preset.
        local = page.evaluate("() => LOCAL_ENGINE_NAMES")
        assert "toy_nearest" in local
        assert "gpt4o_mini_post" not in local
        ctx.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_the_local_preset_runs_only_the_engines_the_server_calls_local(page):
    page.select_option("#engine-preset", "local")
    run_query(page)
    modes = page.evaluate("() => window._lastResult.manifest.selected_engines")
    assert set(modes) == {"contrastgeo", "fewuser", "retrievezero",
                          "gazetteer_post", "gazetteer_user"}


# ----- geography ---------------------------------------------------------------

def _arc(page, a, b, segments=64):
    return page.evaluate("([a, b, n]) => greatCircleArc(a, b, n)", [a, b, segments])


def test_the_consensus_line_is_a_great_circle_not_a_rhumb(page):
    """Every sampled point lies on the great circle between the two ends."""
    arc = _arc(page, SINGAPORE, TOKYO)
    assert len(arc) >= 33, "at least 32 segments"
    total = page.evaluate("([a, b]) => haversineKm(a, b)", [SINGAPORE, TOKYO])
    for point in arc:
        legs = page.evaluate(
            "([a, p, b]) => haversineKm(a, p) + haversineKm(p, b)",
            [SINGAPORE, point, TOKYO])
        # On the great circle the two legs sum to the whole; a rhumb does not.
        assert abs(legs - total) < 1.0, point


def test_the_line_takes_the_short_way_over_the_antimeridian(page):
    """Tokyo to San Francisco is drawn across the Pacific and not across Eurasia and the Atlantic."""
    arc = _arc(page, TOKYO, SAN_FRANCISCO)
    # Every point is in the Pacific half, north of the equator: the old line
    # ran west across Eurasia and Africa.
    for lat, lon in arc:
        assert lat > 30, (lat, lon)
        assert lon > 130 or lon < -110, (lat, lon)
    # Drawn, the path is wrapped into one longitude frame, so it never jumps
    # a full turn at the dateline. Raw, it does.
    raw_jumps = sum(1 for a, b in zip(arc, arc[1:], strict=False) if abs(b[1] - a[1]) > 180)
    assert raw_jumps == 1
    drawn = page.evaluate("([pts, ref]) => wrapForFit(pts, ref)", [arc, TOKYO[1]])
    assert all(abs(b[1] - a[1]) < 5 for a, b in zip(drawn, drawn[1:], strict=False))
    assert max(lon for _, lon in drawn) > 180, "the frame runs past the dateline"


PACIFIC_PAIR = """() => {
  SERVER_COORDS["Tokyo"] = [35.6762, 139.6503];
  SERVER_COORDS["San Francisco"] = [37.7749, -122.4194];
  const one = (city) => ({
    consensus_city: city, consensus_confidence: 0.5,
    contributing_engines: ["gazetteer_post"], method: "weighted",
  });
  window._lastResult = { manifest: { flag_radius_km: 161 } };
  renderMap(
    { post: one("Tokyo"), user: one("San Francisco") },
    { post_consensus_city: "Tokyo", user_consensus_city: "San Francisco",
      disagreement_flag: true, disagreement_km: 8274.6, notes: ["far apart."] },
    {});
  const drawn = { markers: [], line: [] };
  markerLayer.getLayers().forEach(layer => {
    if (layer.getLatLng) drawn.markers.push(layer.getLatLng().lng);
    else if (layer.getLatLngs) drawn.line = layer.getLatLngs().map(p => p.lng);
  });
  drawn.center = map.getCenter().lng;
  drawn.zoom = map.getZoom();
  return drawn;
}"""


def test_a_pacific_pair_is_drawn_and_framed_over_the_pacific(page):
    """Placeholder places are random, so this test fixes the coordinates."""
    drawn = page.evaluate(PACIFIC_PAIR)
    # One longitude frame: Tokyo at 139.65 and San Francisco at 237.58.
    assert drawn["markers"], "no marker was drawn"
    assert all(139 <= lng <= 240 for lng in drawn["markers"]), drawn["markers"]
    assert drawn["line"], "no line was drawn"
    assert all(abs(b - a) < 5 for a, b in zip(drawn["line"], drawn["line"][1:], strict=False))
    assert min(drawn["line"]) > 135 and max(drawn["line"]) > 180
    # The view sits over the Pacific, not over Algeria at 8.6 east.
    centre = drawn["center"] % 360
    assert 160 < centre < 210, centre


def test_the_view_for_a_pacific_pair_centres_on_the_pacific(page):
    """A fit on raw longitudes would centre the view on Algeria."""
    wrapped = page.evaluate("(pts) => wrapForFit(pts)", [TOKYO, SAN_FRANCISCO])
    centre = sum(lon for _, lon in wrapped) / len(wrapped)
    # 139.65 and 237.58 average to 188.6, which is 171 west: the Pacific.
    assert 170 < centre < 200, centre


def test_the_flag_radius_is_a_geodesic_circle(page):
    """A Leaflet circle is an ellipse in the projection, so the radius is drawn as a geodesic polygon."""
    ring = page.evaluate("() => geodesicCircle([64.1466, -21.9426], 161, 72)")
    assert len(ring) == 73
    for point in ring:
        km = page.evaluate("([c, p]) => haversineKm(c, p)",
                           [[64.1466, -21.9426], point])
        assert abs(km - 161) < 0.5, (point, km)


def test_the_flag_radius_is_not_drawn_when_it_would_be_a_smudge(page):
    """At the fitted zoom for an intercontinental pair the radius would be about 5 px wide."""
    run_query(page)
    shown = page.evaluate(
        "() => [...document.querySelectorAll('#map-legend .legend-item')]"
        ".filter(el => !el.classList.contains('hidden'))"
        ".map(el => el.dataset.legend)")
    px = page.evaluate("() => _radiusRequest ? "
                       "radiusPixels(_radiusRequest.center, _radiusRequest.km) : 0")
    assert px < 15
    assert "radius" not in shown, shown
    # And the legend names nothing else that is not on the map.
    assert set(shown) <= {"fused", "consensus", "muted", "line"}
    for key in shown:
        assert page.evaluate("(k) => _legendKeys.has(k)", key)


def test_the_flag_states_the_distance_method_from_the_instance(page):
    """The flag names the distance formula, the Earth radius and the threshold that the instance uses."""
    run_query(page)
    # It sits inside the flag's expandable note, so it is in the document
    # before the reader opens it.
    line = page.text_content("#flag-distance-method")
    assert "haversine" in line
    assert "6371.0088" in line
    assert "161 km" in line


def test_no_target_is_smaller_than_24_by_24(browser, instance):
    """Markers, the candidate summary and the attribution links all meet the 24 px minimum."""
    ctx = browser.new_context(viewport={"width": 375, "height": 700})
    page = ctx.new_page()
    page.goto(instance, wait_until="networkidle")
    try:
        run_query(page)
        small = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('a, button, input, select, summary, [tabindex]')
            .forEach(el => {
              const r = el.getBoundingClientRect();
              if (r.width === 0 || r.height === 0) return;
              if (r.width < 24 || r.height < 24) {
                out.push(`${el.tagName}#${el.id || '-'} ` +
                         `${Math.round(r.width)}x${Math.round(r.height)}`);
              }
            });
          return out;
        }""")
        assert small == [], small
    finally:
        ctx.close()


def test_the_basemap_is_attributed_the_way_the_licence_requires(page):
    """The attribution control links to the OpenStreetMap copyright page."""
    attribution = page.inner_text(".leaflet-control-attribution")
    assert "OpenStreetMap contributors" in attribution
    href = page.get_attribute(
        '.leaflet-control-attribution a[href*="openstreetmap.org/copyright"]', "href")
    assert href == "https://www.openstreetmap.org/copyright"
    urls = page.evaluate("() => { const out = []; "
                         "document.querySelectorAll('img.leaflet-tile').forEach("
                         "t => out.push(t.src)); return out; }")
    assert urls, "no tile was requested"
    for url in urls:
        assert url.startswith("https://tile.openstreetmap.org/"), url


# ----- assistive technology -----------------------------------------------------

def test_the_map_does_not_hide_its_own_controls(page):
    """The map container keeps its zoom controls and markers reachable by assistive technology."""
    run_query(page)
    assert page.get_attribute("#map", "role") == "group"
    assert page.get_attribute("#map", "aria-labelledby") == "map-heading"
    assert "map-list" in (page.get_attribute("#map", "aria-describedby") or "")
    names = page.evaluate(
        "() => [...document.querySelectorAll('#map .leaflet-marker-icon')]"
        ".map(el => el.getAttribute('aria-label'))")
    assert names, "no marker on the map"
    for name in names:
        assert name and len(name) > 3, names
        assert "consensus" in name or "fused prediction" in name, name


def test_the_coordinate_list_is_a_list_under_a_heading(page):
    run_query(page)
    assert page.eval_on_selector("#map-list", "el => el.tagName") == "UL"
    items = page.locator("#map-list > li").count()
    assert items >= 2
    assert page.inner_text("#map-list-heading")


def test_only_the_flag_and_the_short_summary_are_live_regions(page):
    """A query announces the flag and one short summary, and nothing else in the pane is a live region."""
    run_query(page)
    assert page.get_attribute(".right-pane", "aria-live") is None
    assert page.get_attribute(".right-pane", "role") == "region"
    assert page.get_attribute("#disagreement-banner", "role") == "status"
    assert page.inner_text("#flag-heading") == "Verification flag"
    # The short summary stays: it is the one live region on the pane.
    summary = page.inner_text("#map-summary")
    assert "fused prediction" in summary and "km apart" in summary
    assert page.get_attribute("#map-summary", "aria-live") == "polite"
    assert len(summary) < 400


def test_heading_navigation_reaches_the_map_the_flag_and_the_list(page):
    run_query(page)
    headings = page.evaluate(
        "() => [...document.querySelectorAll('.right-pane h2, .right-pane h3')]"
        ".filter(h => h.offsetParent !== null || h.id === 'results-heading')"
        ".map(h => h.textContent.trim())")
    for wanted in ("Result", "Map", "Verification flag",
                   "Predicted places and their coordinates"):
        assert wanted in headings, headings


def test_focus_moves_to_the_answer_and_to_the_drafted_form(page):
    """Focus moves to the result heading after a query and to the form after a draft."""
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.fill("#user_posts", OSINT_TIMELINE)
    page.focus("#geolocate-btn")
    page.click("#geolocate-btn")
    page.wait_for_selector("#disagreement-banner:not(.hidden)", timeout=120000)
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.activeElement.id") == "disagreement-banner"

    page.click("#tab-onboard")
    page.fill("#onboard_city", "Bidadari Estate")
    page.fill("#onboard_region", "Singapore")
    page.focus("#onboard-btn")
    page.click("#onboard-btn")
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.activeElement.id") == "onboard_aliases"


def test_focus_stays_where_the_visitor_is_typing(page):
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.focus("#user_posts")          # the visitor carries on typing
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.activeElement.id") == "user_posts"


def test_every_form_control_has_a_name(page):
    """The draft textareas and the CSV picker each have an accessible name."""
    page.click("#tab-onboard")
    page.fill("#onboard_city", "Bidadari Estate")
    page.fill("#onboard_region", "Singapore")
    page.click("#onboard-btn")
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
    unnamed = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('input, textarea, select').forEach(el => {
        if (el.offsetParent === null && el.type !== 'file') return;
        const byFor = el.id && document.querySelector(`label[for="${el.id}"]`);
        const byWrap = el.closest('label');
        const named = el.getAttribute('aria-label') ||
          el.getAttribute('aria-labelledby') || byFor || byWrap;
        if (!named) out.push(el.id || el.name || el.tagName);
      });
      return out;
    }""")
    assert unnamed == [], unnamed


def test_the_mode_switcher_is_a_real_tablist(page):
    """The five mode buttons form a tablist that states which tab is selected."""
    tabs = ["tab-post", "tab-user", "tab-verify", "tab-onboard", "tab-batch"]
    assert page.locator(".mode-tab").count() == len(tabs)
    for tab in tabs:
        assert page.get_attribute(f"#{tab}", "role") == "tab"
    assert page.get_attribute("#tab-post", "aria-selected") == "true"
    for tab in tabs[1:]:
        assert page.get_attribute(f"#{tab}", "aria-selected") == "false"
    for tab in tabs[:4]:
        assert page.get_attribute(f"#{tab}", "aria-controls") == "panel-demo"
    assert page.get_attribute("#tab-batch", "aria-controls") == "batch-panel"
    # The panel four tabs share names whichever of them is selected.
    assert page.get_attribute("#panel-demo", "aria-labelledby") == "tab-post"

    page.focus("#tab-post")
    for index, tab in enumerate(tabs[1:], start=1):
        page.keyboard.press("ArrowRight")
        assert page.get_attribute(f"#{tab}", "aria-selected") == "true"
        assert page.evaluate("() => document.activeElement.id") == tab
        assert page.get_attribute(f"#{tabs[index - 1]}", "aria-selected") == "false"
    assert page.is_visible("#batch-panel")
    assert page.is_hidden("#panel-demo")
    page.keyboard.press("End")
    assert page.get_attribute("#tab-batch", "aria-selected") == "true"
    page.keyboard.press("Home")
    assert page.get_attribute("#tab-post", "aria-selected") == "true"
    assert page.is_visible("#panel-demo")
    assert page.is_hidden("#batch-panel")


def test_each_tab_shows_the_boxes_its_task_reads(page):
    """The post tab shows one box, the user tab the other, verification both."""
    page.click("#tab-post")
    assert page.is_visible("#post") and page.is_hidden("#user_posts")
    assert page.inner_text('label[for="post"]') == "Post text"
    page.click("#tab-user")
    assert page.is_hidden("#post") and page.is_visible("#user_posts")
    assert page.inner_text('label[for="user_posts"]') == "Recent posts, one per line"
    page.click("#tab-verify")
    assert page.is_visible("#post") and page.is_visible("#user_posts")
    assert page.inner_text('label[for="post"]') == "Post Geolocation"
    assert page.inner_text('label[for="user_posts"]') == (
        "User Geolocation (Recent posts, one per line)")
    page.click("#tab-onboard")
    assert page.is_hidden("#post") and page.is_hidden("#user_posts")
    assert page.is_visible("#onboard_city")
    assert page.is_hidden("#geolocate-btn")


def _intercept_geolocate(page) -> list:
    """Collect the body of every /geolocate request the page sends."""
    sent: list = []

    def handler(route):
        sent.append(route.request.post_data_json)
        route.continue_()

    page.route("**/geolocate", handler)
    return sent


def test_only_the_boxes_a_tab_shows_are_sent(page):
    """The post tab sends no recent posts, the user tab no post, verification both."""
    sent = _intercept_geolocate(page)
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.fill("#user_posts", OSINT_TIMELINE)

    page.click("#tab-post")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert sent[-1]["post"] == OSINT_POST
    assert sent[-1]["user_posts"] is None

    page.click("#tab-user")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert sent[-1]["post"] is None
    assert sent[-1]["user_posts"] == OSINT_TIMELINE.split("\n")

    page.click("#tab-verify")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert sent[-1]["post"] == OSINT_POST
    assert sent[-1]["user_posts"] == OSINT_TIMELINE.split("\n")
    page.unroute("**/geolocate")


def test_only_the_level_a_query_ran_is_expanded(page):
    """A group whose engines were stood down stays closed and says so."""
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.fill("#user_posts", OSINT_TIMELINE)

    page.click("#tab-post")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert _groups_open(page) == {"post": True, "user": False}
    assert _group_summaries(page)["user"] == "User-level engines (5), not run"
    assert _group_summaries(page)["post"] == "Post-level engines (4)"
    # The one-line note stands in for the level with no card.
    assert "No user-level fused prediction" in page.inner_text("#ensembles")

    page.click("#tab-user")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert _groups_open(page) == {"post": False, "user": True}
    assert _group_summaries(page)["post"] == "Post-level engines (4), not run"

    page.click("#tab-verify")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert _groups_open(page) == {"post": True, "user": True}
    assert _group_summaries(page)["post"] == "Post-level engines (4)"
    assert _group_summaries(page)["user"] == "User-level engines (5)"

    # A verification query with one box empty opens only the level that ran.
    page.fill("#user_posts", "")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert _groups_open(page) == {"post": True, "user": False}


def test_a_group_the_visitor_opened_is_reset_by_the_next_result(page):
    """The rule is re-applied on each answer, not on each press."""
    page.click("#tab-post")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    page.evaluate(
        "() => document.querySelector('.engine-group[data-bucket=\"user\"]')"
        ".open = true")
    assert _groups_open(page)["user"] is True
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert _groups_open(page) == {"post": True, "user": False}


def _explanations_open(page) -> list:
    return page.evaluate(
        "() => [document.getElementById('fusion-note').open,"
        " document.querySelector('.disagreement-why').open]")


def test_the_explanations_are_closed_again_by_the_next_result(page):
    """The fusion note and the flag's causes explain, so no answer inherits them."""
    run_query(page)
    assert _explanations_open(page) == [False, False]
    page.evaluate(
        "() => { document.getElementById('fusion-note').open = true;"
        " document.querySelector('.disagreement-why').open = true; }")
    assert _explanations_open(page) == [True, True]
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert _explanations_open(page) == [False, False]
    # Candidate places belongs to the input, not the result, and is left alone.
    page.evaluate(
        "() => { document.getElementById('catalogue-details').open = true; }")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert page.evaluate("() => document.getElementById('catalogue-details').open")


def test_typed_text_survives_a_tab_switch(page):
    """A box keeps what the visitor typed while the tabs change under it."""
    page.click("#tab-verify")
    page.fill("#post", "Fire at the Bedok hawker centre")
    page.fill("#user_posts", "Shinjuku always crowded")
    for tab in ("tab-post", "tab-user", "tab-onboard", "tab-batch", "tab-verify"):
        page.click(f"#{tab}")
    assert page.input_value("#post") == "Fire at the Bedok hawker centre"
    assert page.input_value("#user_posts") == "Shinjuku always crowded"


def test_an_empty_box_is_refused_in_the_words_of_its_tab(page):
    """The refusal names the post on the post tab and the recent posts on the user tab."""
    page.click("#tab-post")
    page.click("#geolocate-btn")
    page.wait_for_selector("#demo-error:not(.hidden)", timeout=30000)
    assert page.inner_text("#demo-error") == "Enter a post, or pick a scenario above."
    page.click("#tab-user")
    page.click("#geolocate-btn")
    page.wait_for_selector("#demo-error:not(.hidden)", timeout=30000)
    assert page.inner_text("#demo-error") == (
        "Enter the account's recent posts, or pick a scenario above.")


def test_verification_with_no_timeline_says_no_flag_is_computed(page):
    """With one box empty the note under the result explains the missing flag."""
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.fill("#user_posts", "")
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert page.locator("#disagreement-banner").is_hidden()
    note = page.text_content("#i-bucket-user")
    assert "no verification flag is computed either" in note
    assert "No user-level fused prediction" in page.inner_text("#ensembles .bucket-note")


def test_a_stored_demo_mode_opens_post_geolocation(browser, instance):
    """An earlier visit stored the one query view these three tabs replaced."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(instance, wait_until="networkidle")
        page.evaluate("() => localStorage.setItem('geolens.mode', 'demo')")
        page.reload(wait_until="networkidle")
        assert page.get_attribute("#tab-post", "aria-selected") == "true"
        assert page.evaluate("() => localStorage.getItem('geolens.mode')") == "post"
    finally:
        ctx.close()


def test_the_active_scenario_tile_says_it_is_active(page):
    tile = page.locator('.scenario-tile[data-scenario-id="osint-credibility"]')
    assert tile.get_attribute("aria-pressed") == "false"
    tile.click()
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert tile.get_attribute("aria-pressed") == "true"
    other = page.locator('.scenario-tile[data-scenario-id="estate-management"]')
    assert other.get_attribute("aria-pressed") == "false"


def test_the_tables_carry_captions_column_and_row_headers(page):
    """Every table has a caption, and its header cells carry a scope."""
    run_batch(page)
    report = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('#batch-panel table').forEach(t => {
        out.push({
          caption: !!t.querySelector('caption'),
          cols: [...t.querySelectorAll('thead th')].every(
            th => th.getAttribute('scope') === 'col'),
          rows: [...t.querySelectorAll('tbody tr')].every(
            tr => tr.firstElementChild.tagName === 'TH' &&
                  tr.firstElementChild.getAttribute('scope') === 'row'),
        });
      });
      return out;
    }""")
    assert report, "no table rendered"
    for entry in report:
        assert entry["caption"] and entry["cols"] and entry["rows"], entry
    titles = page.evaluate(
        "() => [...document.querySelectorAll('#batch-summary abbr')]"
        ".map(a => [a.textContent, a.title])")
    assert any(t == "Acc@1" and len(title) > 20 for t, title in titles), titles
    assert any(t == "Acc@5" for t, _ in titles), titles


def test_there_is_a_skip_link_past_the_header(page):
    """The first focusable element is a link that skips to the main content."""
    page.keyboard.press("Tab")
    assert page.evaluate(
        "() => document.activeElement.className") == "skip-link"
    assert page.evaluate("() => document.activeElement.getAttribute('href')") == "#content"
    assert page.evaluate(
        "() => document.activeElement.getBoundingClientRect().top") >= 0


def test_the_scenario_text_declares_its_language(page):
    """The Indonesian scenario text carries its language so that a screen reader pronounces it correctly."""
    page.click('.scenario-tile[data-scenario-id="crisis-response"]')
    page.wait_for_function(
        "() => document.getElementById('post').getAttribute('lang') === 'id'",
        timeout=60000)
    assert "Banjir" in page.input_value("#post")
    assert page.get_attribute("#user_posts", "lang") == "id"
    # Typing makes the scenario's claim about the box untrue, so it goes.
    page.fill("#post", "a post of my own")
    assert page.get_attribute("#post", "lang") is None


def test_the_cold_start_preset_runs_its_walkthrough(page):
    """The estate scenario: query, draft, operator edits, save, then replay."""
    # The instance is shared between these tests, and an earlier one may have
    # left the place onboarded. The preset asks whether to remove it;
    # accepting replays the cold start, which is what this test drives.
    page.on("dialog", lambda dialog: dialog.accept())
    page.evaluate("() => resetOnboarded('Bidadari Estate')")
    page.wait_for_timeout(300)
    page.click('.scenario-tile[data-scenario-id="estate-management"]')
    assert page.locator(
        '.scenario-tile[data-scenario-id="estate-management"]'
    ).get_attribute("aria-pressed") == "true"
    page.wait_for_function(
        "() => document.getElementById('result-stage').textContent"
        ".startsWith('Before onboarding')", timeout=120000)
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=120000)
    # Onboard adds the place and drafts its profile in one call, so the
    # caption at that step says both; Save and use stores the edits.
    page.wait_for_function(
        "() => document.getElementById('result-stage').textContent"
        ".startsWith('Drafted a profile')", timeout=180000)
    page.wait_for_function(
        "() => document.getElementById('result-stage').textContent"
        ".startsWith('Saved:')", timeout=180000)
    # The save runs on the onboarding tab, which is where the form is read.
    page.click("#tab-onboard")
    assert "Bidadari Estate" in page.input_value("#onboard_city")
    assert "Bidadari" in page.input_value("#onboard_aliases")
    assert page.input_value("#onboard_lat") == "1.3396"
    # Replay sits with the query controls, on the tab the posts run on.
    page.click("#tab-post")
    assert page.is_visible("#replay-btn")


def test_the_english_scenarios_leave_the_document_language_alone(page):
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert page.get_attribute("#post", "lang") is None


def test_a_keyboard_alone_completes_a_query(page):
    """Tab to the post box, type, tab to the button, press it."""
    page.focus("#post")
    page.keyboard.type("Fire at the Bedok hawker centre")
    for _ in range(20):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.id") == "geolocate-btn":
            break
    else:
        pytest.fail("Geolocate is not reachable with Tab")
    page.keyboard.press("Enter")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert page.inner_text("#ensemble-cards")


def test_a_keyboard_alone_completes_an_onboarding_draft(page):
    page.click("#tab-onboard")
    page.focus("#onboard_city")
    page.keyboard.type("Bidadari Estate")
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.id") == "onboard-btn"
    page.keyboard.press("Enter")
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.activeElement.id") == "onboard_aliases"


# ----- reflow -------------------------------------------------------------------

@pytest.mark.parametrize("width", [320, 375])
def test_no_sideways_scrolling_at_phone_width(browser, instance, width):
    """No view is wider than the viewport at phone widths, including the bulk tab's tables."""
    ctx = browser.new_context(viewport={"width": width, "height": 700})
    page = ctx.new_page()
    page.goto(instance, wait_until="networkidle")
    try:
        tabs = ("tab-post", "tab-user", "tab-verify", "tab-onboard", "tab-batch")
        assert page.locator(".mode-tab").count() == len(tabs)
        for tab in tabs:
            page.click(f"#{tab}")
            page.wait_for_timeout(200)
            assert page.evaluate("document.documentElement.scrollWidth") == width, tab
            # The row wraps onto two or three lines rather than shrinking.
            box = page.locator(f"#{tab}").bounding_box()
            assert box["height"] >= 24, (tab, box)
            assert box["x"] + box["width"] <= width, (tab, box)
        # The main action has to be on screen, not floating in grey space.
        box = page.locator("#batch-run-btn").bounding_box()
        assert box["x"] + box["width"] <= width, box
        run_query(page)
        assert page.evaluate("document.documentElement.scrollWidth") == width
    finally:
        ctx.close()


# ----- waiting and errors --------------------------------------------------------

def test_a_running_query_waits_inside_the_result_pane(page):
    """Placeholder rows for the engines asked, the clock and the cold-start line."""
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.wait_for_selector("#result-waiting:not(.hidden)", timeout=10000)
    text = page.inner_text("#result-waiting")
    assert "about 30 s" in text
    # What the wait is, one click away rather than on the line.
    assert "The encoders load on the first call" in page.text_content("#i-waiting")
    assert re.search(r"\b\d+ s\b", text), text
    assert page.locator("#waiting-rows .waiting-row").count() >= 5
    assert "ContrastGeo" in text
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    page.wait_for_selector("#result-waiting", state="hidden", timeout=10000)


def test_onboarding_with_an_empty_name_says_what_is_missing(page):
    """Pressing Onboard with nothing typed names what is missing."""
    page.click("#tab-onboard")
    page.click("#onboard-btn")
    page.wait_for_selector("#onboard-status:not(.hidden)", timeout=5000)
    assert "No place name" in page.inner_text("#onboard-status")
    assert "error" in (page.get_attribute("#onboard-status", "class") or "")
    assert page.evaluate("() => document.activeElement.id") == "onboard-status"


def test_a_second_spelling_of_a_catalogue_place_is_named(page):
    """The 409 says which spelling the catalogue already holds."""
    page.click("#tab-onboard")
    page.fill("#onboard_city", "singapore")
    page.fill("#onboard_region", "Singapore")
    page.click("#onboard-btn")
    page.wait_for_selector("#onboard-status:not(.hidden)", timeout=30000)
    message = page.inner_text("#onboard-status")
    assert "already in the catalogue under the name Singapore" in message


def test_choosing_a_new_file_clears_the_previous_bulk_error(page, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("id,post\n1,\n")
    page.click("#tab-batch")
    page.set_input_files("#batch-file", str(bad))
    page.click("#batch-run-btn")
    page.wait_for_selector("#batch-status.error", timeout=60000)
    assert page.inner_text("#batch-status")
    page.set_input_files("#batch-file", str(EXAMPLE_CSV))
    page.wait_for_timeout(200)
    assert page.is_hidden("#batch-status")


def test_an_error_reads_the_message_out_of_the_envelope(page, tmp_path):
    missing_id = tmp_path / "no-id.csv"
    missing_id.write_text("post\nhello from somewhere\n")
    page.click("#tab-batch")
    page.set_input_files("#batch-file", str(missing_id))
    page.click("#batch-run-btn")
    page.wait_for_selector("#batch-status.error", timeout=60000)
    message = page.inner_text("#batch-status")
    assert "id" in message
    assert "Request failed" not in message, message


# ----- the exports ----------------------------------------------------------------

def run_batch(page) -> None:
    page.click("#tab-batch")
    page.set_input_files("#batch-file", str(EXAMPLE_CSV))
    page.click("#batch-run-btn")
    page.wait_for_selector("#batch-summary:not(.hidden)", timeout=600000)
    page.wait_for_timeout(400)


def _download(page, selector: str, tmp_path: Path) -> Path:
    with page.expect_download() as info:
        page.click(selector)
    out = tmp_path / info.value.suggested_filename
    info.value.save_as(out)
    return out


def test_the_per_row_csv_stands_alone(page, tmp_path):
    """The per-row CSV carries the coordinates as well as every engine's answer."""
    run_batch(page)
    path = _download(page, "#dl-results-csv", tmp_path)
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    assert rows
    header = rows[0].keys()
    for level in ("post", "user"):
        for suffix in ("city", "place_id", "lat", "lon", "error_km", "within_161km"):
            assert f"{level}_fused_{suffix}" in header
    for engine in ("gazetteer_post", "claude_haiku_user", "contrastgeo"):
        for suffix in ("top1", "place_id", "lat", "lon", "error_km", "within_161km"):
            assert f"{engine}_{suffix}" in header
    scored = [r for r in rows if r["gazetteer_post_error_km"]]
    assert scored, "no row carried a distance"
    for row in scored:
        assert row["gazetteer_post_place_id"]
        assert float(row["gazetteer_post_lat"])
        # One decimal, everywhere.
        assert row["gazetteer_post_error_km"].count(".") == 1
        assert len(row["gazetteer_post_error_km"].split(".")[1]) == 1
        assert row["gazetteer_post_within_161km"] in ("0", "1")


def test_the_geojson_is_one_feature_per_row_level_and_engine(page, tmp_path):
    """The GeoJSON holds one feature per row, level and engine, with one property shape."""
    run_batch(page)
    path = _download(page, "#dl-geojson", tmp_path)
    doc = json.loads(path.read_text())

    assert doc["type"] == "FeatureCollection"
    assert "crs" not in doc, "RFC 7946 removed the crs member"
    features = doc["features"]
    assert features

    # One property schema, with explicit nulls, so geopandas types the
    # booleans as booleans rather than as float64 with NaNs.
    shapes = {tuple(sorted(f["properties"])) for f in features}
    assert len(shapes) == 1, shapes
    schema = set(shapes.pop())
    for key in ("row_id", "level", "engine", "engine_label", "is_fused", "place_id",
                "lat", "lon", "error_km", "within_161km", "no_coordinate_reason"):
        assert key in schema, key

    ids = [f["id"] for f in features]
    assert len(ids) == len(set(ids)), "Feature.id must be unique"
    for f in features:
        assert f["id"] == f"{f['properties']['row_id']}:" \
                          f"{f['properties']['level'] or 'row'}:" \
                          f"{f['properties']['engine'] or 'none'}"
        assert isinstance(f["properties"]["is_fused"], bool)
        for flag in ("within_161km", "names_a_catalogue_place", "disagreement_flag"):
            assert f["properties"][flag] is None or isinstance(f["properties"][flag], bool)
        if f["geometry"] is None:
            assert f["properties"]["no_coordinate_reason"]
        else:
            assert f["geometry"]["type"] == "Point"
            lon, lat = f["geometry"]["coordinates"]
            assert -180 <= lon <= 180 and -90 <= lat <= 90
            assert f["properties"]["lat"] == lat and f["properties"]["lon"] == lon

    engines = {f["properties"]["engine"] for f in features}
    assert "fused" in engines
    assert {"gazetteer_post", "gazetteer_user", "contrastgeo"} <= engines
    errors = [f["properties"]["error_km"] for f in features
              if f["properties"]["error_km"] is not None]
    assert errors
    for km in errors:
        assert round(km, 1) == km, km


# ----- the wait, and what a failure leaves on screen -------------------------------

def _delay_geolocate(page, ms: int) -> None:
    """Hold /geolocate in the page for `ms`, so the wait can be watched."""
    page.evaluate(
        """(ms) => {
          const real = window.fetch;
          window.fetch = (input, init) => {
            const url = String(typeof input === 'string' ? input : input.url);
            if (!url.includes('/geolocate')) return real(input, init);
            return new Promise(r => setTimeout(() => r(real(input, init)), ms));
          };
        }""",
        ms,
    )


def test_the_waiting_state_survives_a_twenty_second_query(page):
    _delay_geolocate(page, 20000)
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.wait_for_selector("#result-waiting:not(.hidden)", timeout=5000)
    page.wait_for_timeout(5000)
    assert re.search(r"\b[5-9] s\b|\b1\d s\b", page.inner_text("#waiting-elapsed"))
    # The pane is in view rather than below the fold.
    box = page.locator("#result-waiting").bounding_box()
    assert box and box["y"] < 900, box
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=60000)


def test_a_refused_query_retires_the_previous_answer(page):
    run_query(page)
    assert not page.locator("#ensembles").is_hidden()
    page.fill("#post", "x" * 33000)
    page.fill("#user_posts", "")
    page.click("#geolocate-btn")
    page.wait_for_selector("#demo-error:not(.hidden)", timeout=30000)
    assert page.locator("#ensembles").is_hidden()
    assert page.locator("#disagreement-banner").is_hidden()


def test_a_refusal_is_a_sentence_not_a_field_path(page):
    page.click("#tab-verify")
    page.fill("#post", "x" * 33000)
    page.click("#geolocate-btn")
    page.wait_for_selector("#demo-error:not(.hidden)", timeout=30000)
    message = page.inner_text("#demo-error")
    assert message == "The post is longer than the 2,000-character limit."
    assert "post:" not in message


def test_the_boxes_count_characters_against_the_instance_cap(page):
    page.click("#tab-verify")
    page.fill("#post", "x" * 2001)
    page.wait_for_timeout(200)
    assert page.inner_text("#post-count") == "2,001 / 2,000"
    assert "over" in (page.get_attribute("#post-count", "class") or "")
    page.fill("#user_posts", "ab\ncde")
    page.wait_for_timeout(200)
    assert page.inner_text("#user_posts-count") == "5 / 20,000"


def test_an_html_error_page_becomes_one_plain_sentence(page):
    page.route("**/geolocate", lambda route: route.fulfill(
        status=502, content_type="text/html",
        body="<html><body>502 Bad Gateway</body></html>"))
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.wait_for_selector("#demo-error:not(.hidden)", timeout=30000)
    message = page.inner_text("#demo-error")
    assert message == ("The instance did not answer (502). It may be restarting; "
                       "try again in a minute.")
    assert "<html>" not in message
    page.unroute("**/geolocate")


# ----- preset controls -------------------------------------------------------------

def test_a_preset_stops_at_the_step_that_failed_and_offers_retry(page):
    page.on("dialog", lambda dialog: dialog.accept())
    page.route("**/onboard", lambda route: route.abort()
               if route.request.method == "POST" else route.continue_())
    page.click('.scenario-tile[data-scenario-id="estate-management"]')
    page.wait_for_selector("#preset-retry-btn:not(.hidden)", timeout=180000)
    step = page.inner_text("#preset-step")
    assert step.startswith("Scenario stopped at step 2 of 6"), step
    assert "could not be reached" in step
    # No further post is cycled into the box once the preset has stopped.
    before = page.input_value("#post")
    page.wait_for_timeout(4000)
    assert page.input_value("#post") == before
    assert page.locator("#preset-pause-btn").is_hidden()
    page.unroute("**/onboard")


def test_a_preset_names_its_step_and_pause_holds_it(page):
    page.click('.scenario-tile[data-scenario-id="crisis-response"]')
    page.wait_for_function(
        r"() => /^Step \d+ of \d+: /.test("
        "document.getElementById('preset-step').textContent)", timeout=120000)
    page.click("#preset-pause-btn")
    assert page.inner_text("#preset-pause-btn") == "Resume"
    page.wait_for_timeout(200)
    held = page.inner_text("#preset-step")
    page.wait_for_timeout(6000)
    assert page.inner_text("#preset-step") == held
    page.click("#preset-next-btn")
    page.wait_for_function(
        "(held) => document.getElementById('preset-step').textContent !== held",
        arg=held, timeout=120000)


def _groups_open(page) -> dict:
    return page.evaluate(
        "() => Object.fromEntries("
        "[...document.querySelectorAll('.engine-group')]"
        ".map(g => [g.dataset.bucket, g.open]))")


def _group_summaries(page) -> dict:
    return page.evaluate(
        "() => Object.fromEntries("
        "[...document.querySelectorAll('.engine-group')]"
        ".map(g => [g.dataset.bucket, g.querySelector('summary').innerText.trim()]))")


def test_the_group_of_each_level_that_ran_is_open_and_the_reach_is_summarised(page):
    page.on("dialog", lambda dialog: dialog.accept())
    page.click('.scenario-tile[data-scenario-id="crisis-response"]')
    page.wait_for_selector("#reach-summary:not(.hidden)", timeout=180000)
    summary = page.inner_text("#reach-summary")
    assert summary.startswith("Reached Sintang:"), summary
    assert "post-level engines" in summary and "user-level engines" in summary
    # Every post of this scenario carries a timeline, so both levels ran and
    # both groups are open.
    assert _groups_open(page) == {"post": True, "user": True}


def test_a_level_that_was_not_run_is_left_out_of_the_reach_summary(page):
    """The estate posts carry no timeline, so "0 of 5" would read as 5 failures."""
    page.on("dialog", lambda dialog: dialog.accept())
    page.click('.scenario-tile[data-scenario-id="estate-management"]')
    page.wait_for_selector("#reach-summary:not(.hidden)", timeout=180000)
    summary = page.inner_text("#reach-summary")
    assert summary.startswith("Reached Bidadari Estate:"), summary
    assert "post-level engines" in summary
    assert "user-level engines" not in summary


def _selected_tab(page) -> str:
    return page.evaluate(
        "() => (document.querySelector('.mode-tab[aria-selected=\"true\"]')"
        " || {}).id || ''")


def _start_preset_and_wait_for_step_one(page, scenario_id: str) -> None:
    page.on("dialog", lambda dialog: dialog.accept())
    page.click(f'.scenario-tile[data-scenario-id="{scenario_id}"]')
    page.wait_for_function(
        r"() => /^Step 1 of \d+: /.test("
        "document.getElementById('preset-step').textContent)", timeout=180000)


def _walk_to_the_end_of_the_preset(page, timeout: float = 300.0) -> None:
    """Press Next step until the preset says it has finished.

    A press is ignored while the step's request is in flight, so each one
    waits for the busy state to clear first. A preset that has stopped never
    reaches the last step, and that is a failure rather than a wait.
    """
    idle = "() => !document.querySelector('[aria-busy=\"true\"]')"
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = page.inner_text("#preset-step")
        if line.startswith("Finished"):
            return
        assert not line.startswith("Scenario stopped"), line
        page.wait_for_function(idle, timeout=180000)
        button = page.locator("#preset-next-btn")
        if button.is_visible() and not button.is_disabled():
            button.click()
        page.wait_for_timeout(200)
    raise AssertionError(
        f"the preset did not finish: {page.inner_text('#preset-step')}")


def test_a_tab_the_visitor_picks_stops_a_running_preset(page):
    """A preset never pulls the visitor off a tab they chose themselves."""
    page.evaluate("() => resetOnboarded('Bidadari Estate')")
    page.wait_for_timeout(300)
    _start_preset_and_wait_for_step_one(page, "estate-management")
    page.click("#tab-batch")
    # Long enough for the step that was in flight to finish and the next one
    # to have switched tabs, had the preset survived.
    page.wait_for_timeout(9000)
    assert _selected_tab(page) == "tab-batch"
    assert page.is_visible("#batch-panel")
    tile = page.locator('.scenario-tile[data-scenario-id="estate-management"]')
    assert tile.get_attribute("aria-pressed") == "false"
    page.click("#tab-post")
    assert page.inner_text("#preset-step") == "Scenario stopped."
    assert page.locator("#preset-pause-btn").is_hidden()
    assert page.locator("#preset-next-btn").is_hidden()
    page.evaluate("() => resetOnboarded('Bidadari Estate')")


def test_a_second_scenario_cancels_the_first(page):
    page.evaluate("() => resetOnboarded('Bidadari Estate')")
    page.wait_for_timeout(300)
    _start_preset_and_wait_for_step_one(page, "estate-management")
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
    page.wait_for_timeout(1500)
    pressed = page.evaluate(
        "() => [...document.querySelectorAll('.scenario-tile')]"
        ".filter(t => t.getAttribute('aria-pressed') === 'true')"
        ".map(t => t.dataset.scenarioId)")
    assert pressed == ["osint-credibility"], pressed
    # The first scenario's six steps are not counted down any further.
    assert "of 6" not in page.inner_text("#preset-step")
    # The second scenario ends on its own last tab, not on the first's.
    _walk_to_the_end_of_the_preset(page)
    assert _selected_tab(page) == "tab-verify"
    page.evaluate("() => resetOnboarded('Bidadari Estate')")


def test_next_step_says_so_while_a_step_is_running(page):
    """Next step is disabled, and says so, while a request is in flight."""
    _delay_geolocate(page, 8000)
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#preset-controls:not(.hidden)", timeout=30000)
    page.wait_for_selector("#result-waiting:not(.hidden)", timeout=30000)
    assert page.get_attribute("#preset-next-btn", "aria-disabled") == "true"
    assert page.locator("#preset-next-btn").is_disabled()
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)


def test_a_preset_does_not_overwrite_the_tab_the_visitor_stored(page):
    """Only a tab the visitor picks is remembered for the next visit."""
    page.click("#tab-user")
    assert page.evaluate("() => localStorage.getItem('geolens.mode')") == "user"
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
    _walk_to_the_end_of_the_preset(page)
    assert _selected_tab(page) == "tab-verify"
    assert page.evaluate("() => localStorage.getItem('geolens.mode')") == "user"


# A note is part of the block its button sits in: neither may outlive the tab.
NOTES_WITHOUT_A_BUTTON = """() => {
  const out = [];
  document.querySelectorAll('.info-body').forEach(body => {
    if (!body.getClientRects().length) return;
    const button = document.querySelector(
      `.info-btn[aria-controls="${body.id}"]`);
    if (!button || !button.getClientRects().length) out.push(body.id);
  });
  return out;
}"""


@pytest.mark.parametrize(
    "tab", ["tab-post", "tab-user", "tab-verify", "tab-onboard", "tab-batch"])
def test_no_note_is_left_open_on_a_tab_without_its_button(page, tab):
    """An open note goes off the page with the block its button belongs to."""
    for opener in ("tab-post", "tab-user", "tab-verify", "tab-onboard"):
        page.click(f"#{opener}")
        page.evaluate(
            "() => document.querySelectorAll('.info-btn').forEach(b => {"
            "  if (b.getClientRects().length) b.click(); })")
    page.click(f"#{tab}")
    page.wait_for_timeout(200)
    assert page.evaluate(NOTES_WITHOUT_A_BUTTON) == []
    expanded = page.evaluate(
        "() => [...document.querySelectorAll('.info-btn[aria-expanded=\"true\"]')]"
        ".filter(b => !b.getClientRects().length).map(b => b.getAttribute('aria-controls'))")
    assert expanded == [], expanded


def test_the_run_line_names_the_task_the_answer_came_from(page):
    page.click("#tab-post")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    line = page.inner_text("#run-line")
    assert line.startswith("Post Geolocation · k = 5 · "), line
    # One level ran, so there is no pair for the radius to bound.
    assert "flag radius" not in line
    run_query(page)
    line = page.inner_text("#run-line")
    assert line.startswith("Post vs. User Verification · k = 5 · "), line
    assert "flag radius 161 km" in line


def test_an_answer_from_another_tab_says_which_tab_it_came_from(page):
    """The pane is shared by the three tasks, so it is never read as this tab's."""
    page.click("#tab-post")
    page.fill("#post", OSINT_POST)
    page.click("#geolocate-btn")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert page.locator("#result-source").is_hidden()
    page.click("#tab-user")
    assert page.inner_text("#result-source") == "This result is from Post Geolocation."
    # The answer itself stays: a tab switch is not a reason to throw it away.
    assert page.is_visible("#ensembles")
    assert page.evaluate("() => window._lastResult !== null")
    page.click("#tab-post")
    assert page.locator("#result-source").is_hidden()


def test_a_refusal_does_not_travel_to_the_next_tab(page):
    """Each tab words its refusal for its own boxes, so none inherits another's."""
    page.click("#tab-verify")
    page.fill("#post", "")
    page.fill("#user_posts", "")
    page.click("#geolocate-btn")
    page.wait_for_selector("#demo-error:not(.hidden)", timeout=30000)
    page.click("#tab-post")
    assert page.locator("#demo-error").is_hidden()


def test_reset_onboarding_is_shown_where_there_is_something_to_reset(page):
    """It belongs to the onboarding tab, and it is never a press that does nothing."""
    page.evaluate("() => resetOnboarded('Bidadari Estate')")
    page.wait_for_timeout(300)
    page.click("#tab-onboard")
    assert page.locator("#reset-row").is_hidden()
    page.fill("#onboard_city", "Bidadari Estate")
    page.fill("#onboard_region", "Singapore")
    page.click("#onboard-btn")
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
    page.wait_for_timeout(500)
    assert page.is_visible("#reset-row")
    assert page.is_visible("#reset-scenario-btn")
    # No scenario is loaded, so the query tabs have nothing of their own to
    # reset and do not carry the control.
    page.click("#tab-post")
    assert page.locator("#reset-row").is_hidden()
    page.click("#tab-onboard")
    # Its note opens under the row rather than beside the button.
    page.click('.info-btn[aria-controls="i-reset"]')
    top = page.evaluate("""() => {
      const b = document.getElementById('i-reset').getBoundingClientRect();
      const t = document.querySelector('.info-btn[aria-controls="i-reset"]')
        .getBoundingClientRect();
      return [b.top, t.bottom];
    }""")
    assert top[0] >= top[1] - 1, top
    page.click("#reset-scenario-btn")
    page.wait_for_timeout(1000)
    assert page.locator("#reset-row").is_hidden()


def _tabs_through_a_preset(page, scenario_id: str, count: int) -> list[str]:
    """The tab each step of a preset runs on, read as the step numbers move."""
    page.on("dialog", lambda dialog: dialog.accept())
    page.click(f'.scenario-tile[data-scenario-id="{scenario_id}"]')
    seen: list[str] = []
    deadline = time.time() + 600
    while len(seen) < count and time.time() < deadline:
        match = re.match(r"^Step (\d+) of \d+: ", page.inner_text("#preset-step"))
        if not match or int(match.group(1)) != len(seen) + 1:
            page.wait_for_timeout(100)
            continue
        seen.append(_selected_tab(page))
    return seen


@pytest.mark.parametrize("scenario_id,expected", [
    ("estate-management",
     ["tab-post", "tab-onboard", "tab-onboard", "tab-post", "tab-post", "tab-post"]),
    ("crisis-response",
     ["tab-verify", "tab-onboard", "tab-onboard",
      "tab-verify", "tab-verify", "tab-verify"]),
    ("osint-credibility", ["tab-post", "tab-user", "tab-verify"]),
])
def test_a_preset_opens_the_tab_each_step_belongs_to(page, scenario_id, expected):
    """A cold start moves to the onboarding tab and back to its task tab.

    The viral-post preset instead walks the three geolocation tabs, one step
    each.
    """
    page.evaluate("(name) => name && resetOnboarded(name)",
                  {"estate-management": "Bidadari Estate",
                   "crisis-response": "Sintang"}.get(scenario_id))
    page.wait_for_timeout(300)
    assert _tabs_through_a_preset(page, scenario_id, len(expected)) == expected
    # The step line and the controls stay on screen across the switches.
    assert page.is_visible("#preset-step")


# ----- leaving a scenario ----------------------------------------------------------

def _left_the_scenario(page) -> None:
    """The landing state: the boxes and the pane empty, no tile, no bar, focus on the tab."""
    page.wait_for_timeout(400)
    assert _selected_tab(page) == "tab-post"
    assert page.input_value("#post") == ""
    assert page.input_value("#user_posts") == ""
    assert page.locator("#ensembles").is_hidden()
    assert page.locator("#disagreement-banner").is_hidden()
    assert page.locator("#per-engine-section").is_hidden()
    assert page.evaluate("() => window._lastResult === null")
    assert page.locator("#preset-controls").is_hidden()
    assert page.locator("#active-scenario-banner").is_hidden()
    pressed = page.evaluate(
        "() => [...document.querySelectorAll('.scenario-tile')]"
        ".filter(t => t.getAttribute('aria-pressed') === 'true').length")
    assert pressed == 0
    assert page.evaluate("() => document.activeElement.id") == "tab-post"
    assert page.inner_text("#tab-announce") == (
        "Left the scenario. Now on Post Geolocation.")


def test_exit_during_a_running_step_returns_to_the_landing_state(page):
    """Exit is pressed while the step's query is still in flight."""
    _delay_geolocate(page, 8000)
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#result-waiting:not(.hidden)", timeout=30000)
    page.click("#preset-exit-btn")
    _left_the_scenario(page)
    assert page.locator("#result-waiting").is_hidden()


def test_exit_between_two_steps_returns_to_the_landing_state(page):
    """Held between steps by Pause, the scenario is still one press from the landing state."""
    _start_preset_and_wait_for_step_one(page, "crisis-response")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
    page.click("#preset-pause-btn")
    page.click("#preset-exit-btn")
    _left_the_scenario(page)
    page.evaluate("() => resetOnboarded('Sintang')")


def test_exit_after_the_last_step_returns_to_the_landing_state(page):
    """The bar keeps Exit after "Finished", where nothing is running any more."""
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_function(
        "() => document.getElementById('preset-step').textContent"
        ".startsWith('Finished')", timeout=180000)
    assert page.inner_text("#preset-state") == "Scenario finished: Checking a viral post"
    page.click("#preset-exit-btn")
    _left_the_scenario(page)


def test_exit_after_a_manual_stop_returns_to_the_landing_state(page):
    """A tab click stops the scenario and leaves the bar, its state and Exit on screen."""
    _start_preset_and_wait_for_step_one(page, "crisis-response")
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
    page.click("#tab-user")
    page.wait_for_timeout(500)
    assert page.inner_text("#preset-step") == "Scenario stopped."
    assert page.inner_text("#preset-state") == (
        "Scenario stopped: A flooded town outside the catalogue")
    assert page.is_visible("#preset-exit-btn")
    assert page.is_visible("#preset-restart-btn")
    assert page.locator("#preset-pause-btn").is_hidden()
    assert page.locator("#preset-next-btn").is_hidden()
    page.click("#preset-exit-btn")
    _left_the_scenario(page)
    page.evaluate("() => resetOnboarded('Sintang')")


def test_an_answer_that_lands_after_exit_does_not_repaint_the_pane(page):
    """The query left behind is dropped rather than painted over the landing state."""
    _delay_geolocate(page, 6000)
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#result-waiting:not(.hidden)", timeout=30000)
    page.click("#preset-exit-btn")
    page.wait_for_timeout(9000)
    assert page.locator("#ensembles").is_hidden()
    assert page.locator("#disagreement-banner").is_hidden()
    assert page.locator("#demo-error").is_hidden()
    assert page.evaluate("() => window._lastResult === null")
    assert page.input_value("#post") == ""


@pytest.mark.parametrize("scenario_id,title,steps", [
    ("estate-management", "Onboarding a new housing estate", 6),
    ("crisis-response", "A flooded town outside the catalogue", 6),
    ("osint-credibility", "Checking a viral post", 3),
])
def test_the_bar_follows_the_steps_and_the_step_line_keeps_its_format(
        page, scenario_id, title, steps):
    """The label names the scenario, the bar counts its steps, and the step line is unchanged."""
    page.evaluate("(name) => name && resetOnboarded(name)",
                  {"estate-management": "Bidadari Estate",
                   "crisis-response": "Sintang"}.get(scenario_id))
    page.wait_for_timeout(300)
    _start_preset_and_wait_for_step_one(page, scenario_id)
    assert page.inner_text("#preset-state") == f"Scenario in progress: {title}"
    # The line and the bar are read in one call, so a step that ends between
    # two reads cannot make them look out of step with each other.
    read = ("() => { const p = document.getElementById('preset-progress');"
            " return [document.getElementById('preset-step').textContent,"
            " p.value, p.max]; }")
    seen = set()
    deadline = time.time() + 600
    while len(seen) < steps and time.time() < deadline:
        line, value, maximum = page.evaluate(read)
        match = re.match(r"^Step (\d+) of (\d+): .+", line)
        if not match:
            page.wait_for_timeout(100)
            continue
        assert int(match.group(2)) == steps, line
        assert [value, maximum] == [int(match.group(1)), steps], (line, value, maximum)
        seen.add(int(match.group(1)))
    assert seen == set(range(1, steps + 1)), seen
    page.wait_for_function(
        "() => document.getElementById('preset-step').textContent"
        ".startsWith('Finished')", timeout=300000)
    assert page.inner_text("#preset-step") == (
        f"Finished: {steps} {'step' if steps == 1 else 'steps'}.")
    assert page.inner_text("#preset-state") == f"Scenario finished: {title}"
    assert page.evaluate(
        "() => { const p = document.getElementById('preset-progress');"
        " return [p.value, p.max]; }") == [steps, steps]
    page.evaluate("(name) => name && resetOnboarded(name)",
                  {"estate-management": "Bidadari Estate",
                   "crisis-response": "Sintang"}.get(scenario_id))


OSINT_TIMELINE_LINES = [
    "Best ramen in Shibuya tonight",
    "Shinjuku always crowded these days",
    "Got sake at a tiny bar in Asakusa",
]


def test_the_viral_post_preset_sends_one_level_then_the_other_then_both(page):
    """Each of the three steps sends only the boxes its tab shows."""
    sent = _intercept_geolocate(page)
    names: list[str] = []
    captions: list[str] = []
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    deadline = time.time() + 300
    while len(names) < 3 and time.time() < deadline:
        match = re.match(r"^Step (\d+) of 3: (.+)$", page.inner_text("#preset-step"))
        if not match or int(match.group(1)) != len(names) + 1:
            page.wait_for_timeout(100)
            continue
        names.append(match.group(2))
        # The request goes out after the previous answer has been cleared, so
        # waiting for it first keeps the caption read below this step's own.
        while len(sent) < len(names) and time.time() < deadline:
            page.wait_for_timeout(100)
        page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
        page.wait_for_timeout(300)
        captions.append(page.locator("#result-stage").inner_text().strip())
    assert names == ["the post on its own", "the account's recent posts",
                     "the post against its account"], names
    assert len(sent) >= 3, sent
    assert sent[0]["post"] == OSINT_POST and sent[0]["user_posts"] is None
    assert sent[1]["post"] is None and sent[1]["user_posts"] == OSINT_TIMELINE_LINES
    assert sent[2]["post"] == OSINT_POST
    assert sent[2]["user_posts"] == OSINT_TIMELINE_LINES
    assert captions[0] == "The post alone: the post-level engines place it."
    assert captions[1] == "The account alone: the user-level engines place it."
    # The last caption names the flag only when the answer raised one, which
    # placeholder places do not always do.
    flagged = page.locator("#disagreement-banner").is_visible()
    assert captions[2] == (
        "Both together: the two levels disagree, so the flag is raised." if flagged
        else "Both together: the two levels are compared.")
    page.unroute("**/geolocate")


def test_the_viral_post_preset_expands_the_level_each_step_ran(page):
    """Post group, then user group, then both: whichever level the step ran."""
    sent = _intercept_geolocate(page)
    seen: list[dict] = []
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    deadline = time.time() + 300
    while len(seen) < 3 and time.time() < deadline:
        match = re.match(r"^Step (\d+) of 3: ", page.inner_text("#preset-step"))
        if not match or int(match.group(1)) != len(seen) + 1:
            page.wait_for_timeout(100)
            continue
        while len(sent) < len(seen) + 1 and time.time() < deadline:
            page.wait_for_timeout(100)
        page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
        page.wait_for_timeout(300)
        seen.append(_groups_open(page))
    assert seen == [{"post": True, "user": False},
                    {"post": False, "user": True},
                    {"post": True, "user": True}], seen
    page.unroute("**/geolocate")


def test_the_tile_says_in_progress_and_says_nothing_after_exit(page):
    """The tile's state is a word inside it, not the fill colour and aria-pressed alone."""
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#preset-controls:not(.hidden)", timeout=30000)
    tag = page.locator('.scenario-tile[data-scenario-id="osint-credibility"] .tile-tag')
    assert tag.inner_text() == "in progress"
    page.wait_for_function(
        "() => document.getElementById('preset-step').textContent"
        ".startsWith('Finished')", timeout=180000)
    assert tag.inner_text() == "finished"
    page.click("#preset-exit-btn")
    page.wait_for_timeout(400)
    assert tag.is_hidden()
    assert page.evaluate(
        "() => [...document.querySelectorAll('.tile-tag')]"
        ".filter(t => t.textContent).length") == 0


def test_a_place_the_scenario_onboarded_is_still_there_after_exit(page):
    """Exit removes nothing from the shared catalogue, and the landing state says so."""
    page.on("dialog", lambda dialog: dialog.accept())
    page.evaluate("() => resetOnboarded('Bidadari Estate')")
    page.wait_for_timeout(300)
    page.click('.scenario-tile[data-scenario-id="estate-management"]')
    page.wait_for_function(
        "() => document.getElementById('result-stage').textContent"
        ".startsWith('Drafted a profile')", timeout=240000)
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=120000)
    page.click("#preset-exit-btn")
    page.wait_for_timeout(1500)
    assert page.evaluate("() => isOnboarded('Bidadari Estate')")
    assert _selected_tab(page) == "tab-post"
    assert page.inner_text("#leftover-place") == "Bidadari Estate is still onboarded."
    assert page.is_visible("#reset-scenario-btn")
    page.click("#reset-scenario-btn")
    page.wait_for_timeout(1500)
    assert not page.evaluate("() => isOnboarded('Bidadari Estate')")
    assert page.locator("#leftover-place").is_hidden()
    assert page.locator("#reset-row").is_hidden()


def test_exit_leaves_a_recorded_run(browser, instance):
    """The way out of a recorded run is the page without the query parameter."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(f"{instance}/?replay=estate-management", wait_until="networkidle")
        page.wait_for_selector("#preset-controls:not(.hidden)", timeout=30000)
        assert page.inner_text("#preset-state") == (
            "Recorded run in progress: Onboarding a new housing estate")
        page.click("#preset-exit-btn")
        page.wait_for_load_state("networkidle")
        assert "replay" not in page.url
        assert page.locator("#replay-banner").is_hidden()
        assert page.locator(".mode-tab:visible").count() == 5
    finally:
        ctx.close()


def test_the_scenario_bar_does_not_scroll_sideways_at_320(browser, instance):
    """With the bar on screen the page is still 320 wide, and its buttons clear 24 px."""
    ctx = browser.new_context(viewport={"width": 320, "height": 700})
    page = ctx.new_page()
    page.goto(instance, wait_until="networkidle")
    try:
        page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
        page.wait_for_selector("#preset-controls:not(.hidden)", timeout=30000)
        assert page.evaluate("document.documentElement.scrollWidth") == 320
        for button in ("preset-exit-btn", "preset-restart-btn"):
            box = page.locator(f"#{button}").bounding_box()
            assert box["height"] >= 24, (button, box)
            assert box["x"] + box["width"] <= 320, (button, box)
        page.wait_for_selector("#ensembles:not(.hidden)", timeout=180000)
        assert page.evaluate("document.documentElement.scrollWidth") == 320
    finally:
        ctx.close()


def test_a_preset_in_placeholder_mode_says_so(page):
    page.click('.scenario-tile[data-scenario-id="osint-credibility"]')
    page.wait_for_selector("#ensembles:not(.hidden)", timeout=120000)
    assert page.inner_text(".banner-placeholder") == (
        "Placeholder mode: this is not the result reported in the paper.")


# ----- the recorded run ------------------------------------------------------------

def test_a_recorded_run_renders_with_the_api_blocked(browser, instance):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    blocked = []

    def guard(route):
        url = route.request.url
        if re.search(r"/(geolocate|instance|catalogue|onboard|eval|batch)", url):
            blocked.append(url)
            route.abort()
        else:
            route.continue_()

    page.route("**/*", guard)
    try:
        page.goto(f"{instance}/?replay=osint-credibility", wait_until="networkidle")
        page.wait_for_selector("#disagreement-banner:not(.hidden)", timeout=30000)
        assert blocked == [], blocked
        banner = page.inner_text("#replay-banner")
        assert banner.startswith("Recorded run, ")
        assert "Not a live result" in banner
        # Where it was recorded, and what that means for the network, is in
        # the note behind the banner's icon.
        note = page.text_content("#i-replay")
        assert "Recorded from " in note
        assert "Nothing on this page calls an endpoint." in note
        headline = page.inner_text("#disagreement-banner .disagreement-text")
        assert headline == ("Post: Singapore (3 of 4 engines). "
                            "Timeline: Tokyo (4 of 4 engines). 5,311 km apart.")
        assert page.locator("#export-result-btn").is_hidden()
    finally:
        ctx.close()


def test_a_recorded_run_shows_only_the_tab_it_runs_on(browser, instance):
    """A tab a recorded run cannot drive is off the page, as the other two are."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(f"{instance}/?replay=estate-management", wait_until="networkidle")
        page.wait_for_selector("#ensembles:not(.hidden)", timeout=30000)
        shown = page.evaluate(
            "() => [...document.querySelectorAll('.mode-tab')]"
            ".filter(t => t.offsetParent !== null).map(t => t.id)")
        assert shown == ["tab-post"], shown
        # And the arrows have nowhere else to go.
        page.focus("#tab-post")
        page.keyboard.press("ArrowRight")
        assert page.evaluate("() => document.activeElement.id") == "tab-post"
        # Leaving the recorded run puts every tab back.
        page.goto(instance, wait_until="networkidle")
        assert page.locator(".mode-tab:visible").count() == 5
    finally:
        ctx.close()


def test_every_scenario_tile_offers_its_recorded_run(page):
    links = page.locator(".tile-replay")
    assert links.count() == 3
    for index in range(links.count()):
        assert links.nth(index).inner_text() == "Show recorded run"
        assert "?replay=" in (links.nth(index).get_attribute("href") or "")


# ----- what the page says about accuracy, spend and warnings ------------------------

# The benchmark line is two `white-space: nowrap` spans, so it breaks
# between the label and the figures rather than inside them, and the icon
# rides with the figures. The icon's own name is inside the second span, so
# the line as the eye reads it is the text nodes of the two spans.
READ_REFERENCE_LINE = """el => [...el.querySelectorAll('.nowrap')].map(
  s => [...s.childNodes].filter(n => n.nodeType === 3)
        .map(n => n.textContent).join('')).join(' ')"""


def reference_line(page, selector):
    return page.locator(selector).evaluate(READ_REFERENCE_LINE)


def test_every_engine_card_carries_its_benchmark_accuracy(page):
    run_query(page)
    page.evaluate(
        "() => document.querySelector('.engine-group[data-bucket=\"user\"]').open = true")
    cards = page.locator(".engine-card")
    assert cards.count() == 9
    for index in range(cards.count()):
        line = cards.nth(index).locator(".engine-reference")
        assert line.count() == 1
        assert line.evaluate(READ_REFERENCE_LINE).startswith("Benchmark Acc@1 ")
        # The interval and the rows behind it stay in the tooltip as well.
        assert "n=" in (line.get_attribute("title") or "")
    assert reference_line(
        page, '.engine-card[data-engine="contrastgeo"] .engine-reference'
    ) == "Benchmark Acc@1 .75 named · .07 unnamed"
    # The intervals and the rows are one click away; the provenance they
    # share is under the whole section instead of on all nine cards.
    contrast_note = page.text_content('.engine-card[data-engine="contrastgeo"] .info-body')
    assert re.search(r"Named: \.75 \[\.\d\d, \.\d\d\], n=\d+\.", contrast_note), contrast_note
    assert re.search(r"Not named: \.07 \[", contrast_note), contrast_note
    assert "wnut2016-eval-3803853" not in contrast_note
    # The gazetteer abstains when nothing is named, so its rate on those rows
    # is zero and the one recorded hit predates the abstention fix.
    assert reference_line(
        page, '.engine-card[data-engine="gazetteer_post"] .engine-reference'
    ) == "Benchmark Acc@1 .90 named · 0 unnamed"
    gazetteer_note = page.text_content(
        '.engine-card[data-engine="gazetteer_post"] .info-body')
    assert "0 by construction" in gazetteer_note
    assert "predates the abstention fix" in gazetteer_note
    # One footnote under the whole section, saying it is a benchmark figure,
    # and carrying the run and the cost basis every card would repeat.
    provenance = page.locator(".reference-provenance")
    assert provenance.count() == 1
    assert "not a confidence in this answer" in provenance.inner_text()
    provenance_note = page.text_content("#i-provenance")
    assert "wnut2016-eval-3803853" in provenance_note
    assert "list prices" in provenance_note


def test_a_card_note_leads_with_the_badge_when_the_badge_is_an_exception(page):
    """A badge other than "real" is what a reader asks about first."""
    run_query(page)
    card = page.locator('.engine-card[data-engine="contrastgeo"]')
    badge = card.locator(".mode-badge").inner_text()
    note = card.locator(".info-body").text_content()
    if badge == "real":
        assert note.endswith('"real" means the output came from a live model.')
    else:
        assert note.startswith(f'"{badge}" means'), note
    if badge == "placeholder":
        assert "no API key or model is installed" in note
        assert "the answer is a stand-in and no model produced it" in note


def test_the_spend_ceiling_shows_one_notice(browser, instance):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    reason = "The hourly ceiling of $0.50 is reached, so the paid engines stand down."

    def ceiling(route):
        response = route.fetch()
        body = response.json()
        body["spend"] = {
            "estimated_usd_last_hour": 0.51, "estimated_usd_last_day": 0.51,
            "max_usd_per_hour": 0.5, "max_usd_per_day": 5.0,
            "ceiling_reached": True, "reason": reason,
        }
        route.fulfill(response=response, body=json.dumps(body))

    page.route("**/instance", ceiling)
    try:
        page.goto(instance, wait_until="networkidle")
        page.wait_for_selector("#spend-notice:not(.hidden)", timeout=10000)
        assert page.inner_text("#spend-notice") == reason
    finally:
        ctx.close()


def test_a_handle_like_row_id_is_warned_about(page, tmp_path):
    csv_path = tmp_path / "handles.csv"
    csv_path.write_text(
        "id,post,user_posts,ground_truth_city\n"
        "@alice,Fire at Marina Bay Sands Singapore,,Singapore\n")
    page.click("#tab-batch")
    page.set_input_files("#batch-file", str(csv_path))
    page.click("#batch-run-btn")
    page.wait_for_selector("#batch-warnings:not(.hidden)", timeout=300000)
    assert "@alice" in page.inner_text("#batch-warnings")
    assert page.inner_text(".row-id-note") == "Use row ids that do not identify an account."


def test_both_exports_carry_the_catalogue_hash_and_each_engine_mode(page, tmp_path):
    run_batch(page)
    rows = list(csv.DictReader(io.StringIO(
        _download(page, "#dl-results-csv", tmp_path).read_text())))
    assert rows
    assert rows[0]["catalogue_sha"]
    assert rows[0]["gazetteer_post_mode"] in ("real", "stub", "failed",
                                              "no_catalogue_place", "")
    features = json.loads(
        _download(page, "#dl-geojson", tmp_path).read_text())["features"]
    assert all(f["properties"]["catalogue_sha"] for f in features)
    engine_modes = {f["properties"]["engine_mode"] for f in features}
    assert "fused" in engine_modes
    assert engine_modes & {"real", "stub"}


# ----- edit rights on a shared instance --------------------------------------------

def test_a_place_another_browser_drafted_cannot_be_edited_here(browser, tmp_path):
    port_env = {"GEOLENS_REQUIRE_EDIT_TOKEN": "1"}
    base, proc = _start_instance_with(tmp_path, port_env)
    try:
        owner = browser.new_context()
        owner_page = owner.new_page()
        owner_page.goto(base, wait_until="networkidle")
        owner_page.click("#tab-onboard")
        owner_page.fill("#onboard_city", "Bidadari Estate")
        owner_page.fill("#onboard_region", "Singapore")
        owner_page.click("#onboard-btn")
        owner_page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
        owner_page.click("#onboard-save-btn")
        owner_page.wait_for_function(
            "() => document.getElementById('onboard-status').textContent"
            ".startsWith('Saved.')", timeout=30000)

        other = browser.new_context()
        other_page = other.new_page()
        other_page.goto(base, wait_until="networkidle")
        other_page.click("#tab-onboard")
        # Reset onboarding sits with the form it belongs to, and appears once
        # the name in the box is a place that is onboarded now.
        other_page.fill("#onboard_city", "Bidadari Estate")
        other_page.wait_for_selector("#reset-row:not(.hidden)", timeout=20000)
        other_page.click("#reset-scenario-btn")
        other_page.wait_for_selector("#demo-error:not(.hidden)", timeout=20000)
        message = other_page.inner_text("#demo-error")
        assert message.startswith("This place was onboarded from another browser")
        assert "It expires in about" in message
        owner.close()
        other.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_data_handling_is_one_disclosure_on_both_views(page):
    """The full statements sit behind one summary, above the tabs."""
    assert page.locator("#data-handling").count() == 1
    assert page.locator("#data-handling").is_visible()
    assert page.inner_text("#data-handling summary") == "Data handling"
    page.click("#data-handling summary")
    text = page.inner_text("#data-handling")
    for phrase in ("not stored by GeoLens",
                   "OpenAI and Anthropic",
                   "retain API inputs",
                   "listed to every visitor",
                   "later LLM prompts",
                   "IP address",
                   "openstreetmap.org",
                   "Local engines only",
                   "row ids should not be account identifiers"):
        assert phrase in text, phrase
    page.click("#tab-batch")
    assert page.locator("#data-handling").is_visible()
    # The short notices on each view are a few words, not a policy.
    for notice in page.locator(".privacy-notice").all_inner_texts():
        assert len(notice.split()) <= 30, notice


# ----- the info notes ---------------------------------------------------------------

def test_a_note_opens_with_the_keyboard_and_escape_returns_the_focus(page):
    """Enter opens the note beside a label; Escape closes it and comes back."""
    button = page.locator('.info-btn[aria-controls="i-tagline"]')
    button.focus()
    assert page.is_hidden("#i-tagline")
    page.keyboard.press("Enter")
    assert page.is_visible("#i-tagline")
    assert button.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert page.is_hidden("#i-tagline")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.evaluate(
        "() => document.activeElement.getAttribute('aria-controls')") == "i-tagline"


def test_escape_from_inside_a_note_closes_it_and_comes_back(page):
    """The note holds a link, so focus can be inside the body when Escape lands."""
    page.click("#tab-onboard")
    button = page.locator('.info-btn[aria-controls="i-onboard-sharing"]')
    button.click()
    assert page.is_visible("#i-onboard-sharing")
    page.focus("#i-onboard-sharing a")
    page.keyboard.press("Escape")
    assert page.is_hidden("#i-onboard-sharing")
    assert page.evaluate(
        "() => document.activeElement.getAttribute('aria-controls')") == "i-onboard-sharing"


def test_several_notes_can_be_open_at_once(page):
    page.locator('.info-btn[aria-controls="i-tagline"]').click()
    page.locator('.info-btn[aria-controls="i-privacy-demo"]').click()
    assert page.is_visible("#i-tagline")
    assert page.is_visible("#i-privacy-demo")


# Nothing may sit between a button and the body it opens: a screen reader
# reads the two in document order.
NOTHING_BETWEEN = """(id) => {
  const body = document.getElementById(id);
  const button = document.querySelector(`.info-btn[aria-controls="${id}"]`);
  if (!body || !button) return 'missing';
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const between = [];
  let seen = false;
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (button.contains(node)) { seen = true; continue; }
    if (body.contains(node)) break;
    if (seen && node.textContent.trim()) between.push(node.textContent.trim());
  }
  return between;
}"""


@pytest.mark.parametrize("note_id", ["i-frozen", "i-reset", "i-engines", "i-catalogue-rows"])
def test_nothing_is_read_between_a_button_and_its_note(page, note_id):
    assert page.evaluate(NOTHING_BETWEEN, note_id) == []


def test_an_open_note_in_the_button_row_runs_under_the_row(page):
    """Opening it must not widen one control and re-wrap the buttons."""
    before = page.evaluate(
        "() => document.getElementById('geolocate-btn').getBoundingClientRect().top")
    page.locator('.info-btn[aria-controls="i-engines"]').click()
    body, button = page.evaluate("""() => {
      const b = document.getElementById('i-engines').getBoundingClientRect();
      const t = document.querySelector('.info-btn[aria-controls="i-engines"]')
        .getBoundingClientRect();
      return [[b.top, b.left], [t.bottom, t.left]];
    }""")
    assert body[0] >= button[0] - 1          # under the row, not beside it
    assert page.evaluate(
        "() => document.getElementById('geolocate-btn').getBoundingClientRect().top"
    ) == before


def test_the_footer_keeps_each_label_with_its_own_engines(page):
    """"Public baselines:" starts its own group rather than running on."""
    sets = page.locator("footer .footer-set")
    assert sets.count() == 2
    assert sets.nth(0).inner_text().startswith("Frozen encoders:")
    assert sets.nth(1).inner_text().startswith("Public baselines:")
    boxes = page.evaluate("""() => [...document.querySelectorAll('footer .footer-set')]
      .map(s => s.getBoundingClientRect().left)""")
    assert boxes[1] > boxes[0]


def test_the_flag_rate_headline_says_what_the_rate_counts(page):
    page.click("#tab-verify")
    page.fill("#post", OSINT_POST)
    page.fill("#user_posts", OSINT_TIMELINE)
    page.click("#geolocate-btn")
    page.wait_for_selector("#disagreement-banner:not(.hidden)", timeout=120000)
    headline = page.inner_text("#flag-error-rate")
    assert re.match(r"^Flagged \d+% of home-consistent accounts on WNUT-2016\.",
                    headline), headline
    # The server's own sentence is behind the icon, word for word.
    assert "agreed" in page.text_content("#i-flag-rate")


def test_the_coordinate_hint_asks_for_a_check_only_once_there_is_a_pin(page):
    page.click("#tab-onboard")
    page.fill("#onboard_city", "Bidadari Estate")
    page.fill("#onboard_region", "Singapore")
    page.click("#onboard-btn")
    page.wait_for_selector("#onboard-cards:not(.hidden)", timeout=60000)
    page.fill("#onboard_lat", "")
    page.fill("#onboard_lon", "")
    assert page.inner_text("#coord-hint-text") == "No coordinate yet."
    page.fill("#onboard_lat", "1.3396")
    page.fill("#onboard_lon", "103.872")
    assert page.inner_text("#coord-hint-text") == "Check the pin before saving."


def test_the_onboarding_line_carries_the_instruction_and_hides_the_figure(page):
    page.click("#tab-onboard")
    assert page.inner_text("#onboard-panel .panel-note").startswith(
        "Shared with every visitor, so use public names only.")
    note = page.text_content("#i-onboard-sharing")
    assert "An onboarded place" in note
    assert "onboard only names that can be public" in note


def test_each_bulk_table_glosses_its_own_columns(page):
    """The column glosses are hover titles, which a touch screen cannot reach."""
    run_batch(page)
    for body_id, table in (("i-engine-columns", "#engine-summary-table"),
                           ("i-fusion-columns", "#batch-summary .ensemble-table"),
                           ("i-rollup-columns", "#batch-rollup .rollup-table")):
        terms = page.evaluate(
            "(id) => [...document.querySelectorAll(`#${id} dt`)].map(d => d.textContent)",
            body_id)
        glosses = page.evaluate(
            "(id) => [...document.querySelectorAll(`#${id} dd`)].map(d => d.textContent)",
            body_id)
        assert terms, body_id
        titles = page.evaluate(
            "(sel) => [...document.querySelectorAll(`${sel} thead th`)]"
            ".map(th => (th.getAttribute('title') || "
            "(th.querySelector('[title]') || {}).title || '')).filter(Boolean)",
            table)
        # The note says the same thing the hover says, so the two cannot drift.
        assert [g.rstrip(".") for g in glosses] == [t.rstrip(".") for t in titles]


def test_the_bulk_tables_call_the_engines_what_the_cards_call_them(page):
    """The cards say "GPT-4o-mini (post)", so the table does too."""
    run_batch(page)
    names = page.evaluate(
        "() => [...document.querySelectorAll('#engine-summary-table tbody th')]"
        ".map(th => [th.textContent, th.getAttribute('title')])")
    roster = page.evaluate("() => INSTANCE.engines")
    assert names
    for label, registry_id in names:
        assert registry_id in roster, registry_id
        assert label == roster[registry_id]["label"], (label, registry_id)


# Words a reader actually reads: `innerText` counts the accessible names of
# the info buttons, which are there for a screen reader and not for the eye.
VISIBLE_WORDS = """() => {
  const count = (s) => (s || '').split(/\\s+/).filter(Boolean).length;
  // `innerText` falls back to `textContent` for an element that is not
  // rendered, so a collapsed panel's captions would count against it.
  let hidden = 0;
  document.querySelectorAll('.visually-hidden').forEach(el => {
    if (el.getClientRects().length) hidden += count(el.innerText);
  });
  return count(document.body.innerText) - hidden;
}"""

LANDING_WORD_CEILING = 230


def test_the_landing_page_stays_under_its_word_ceiling(page):
    """With every note closed the first screen is a short page, and stays one."""
    page.wait_for_selector(".scenario-tile")
    assert page.locator(".info-body:not(.hidden)").count() == 0
    words = page.evaluate(VISIBLE_WORDS)
    assert words <= LANDING_WORD_CEILING, words
