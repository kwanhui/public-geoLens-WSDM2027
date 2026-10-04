"""The circled "i" and the note it opens, read out of the markup.

These read `index.html` rather than drive a browser, so they hold for the
notes written into the page. The generated ones are covered by
`tests/test_browser_ui.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/geolens/ui/static"
INDEX = (STATIC / "index.html").read_text()
APP_JS = (STATIC / "app.js").read_text()

BUTTON = re.compile(r"<button\b[^>]*class=\"info-btn\"[^>]*>(.*?)</button>", re.S)
BODY = re.compile(
    r"<(?P<tag>div|span)\b[^>]*class=\"info-body[^\"]*\"[^>]*id=\"(?P<id>[^\"]+)\"[^>]*>"
    r"(?P<inner>.*?)</(?P=tag)>", re.S)


def buttons() -> list[str]:
    return [m.group(0) for m in BUTTON.finditer(INDEX)]


def bodies() -> dict[str, str]:
    return {m.group("id"): m.group("inner") for m in BODY.finditer(INDEX)}


def test_the_page_carries_info_notes_at_all() -> None:
    assert len(buttons()) >= 10
    assert len(bodies()) >= 10


@pytest.mark.parametrize("button", buttons(), ids=range(len(buttons())))
def test_every_button_names_a_body_that_exists(button: str) -> None:
    match = re.search(r'aria-controls="([^"]+)"', button)
    assert match, button
    assert match.group(1) in bodies(), match.group(1)


@pytest.mark.parametrize("button", buttons(), ids=range(len(buttons())))
def test_every_button_starts_closed_and_has_a_name(button: str) -> None:
    assert 'aria-expanded="false"' in button
    assert 'type="button"' in button
    assert '<span aria-hidden="true">i</span>' in button
    name = re.search(r'<span class="visually-hidden">About: ([^<]+)</span>', button)
    assert name, button
    assert name.group(1).strip()


def test_no_body_is_empty() -> None:
    """A body is written here, or filled from the instance settings.

    What the region hint is for and what the bulk caps are depend on the
    deployment, so those two are written by app.js instead.
    """
    for note_id, inner in bodies().items():
        if re.sub(r"<[^>]+>", "", inner).strip():
            continue
        assert f'"{note_id}"' in APP_JS, note_id


def test_no_body_is_orphaned() -> None:
    named = {re.search(r'aria-controls="([^"]+)"', b).group(1) for b in buttons()}
    assert set(bodies()) == named


def test_no_button_sits_inside_a_heading_or_the_privacy_notice() -> None:
    """A test collects heading text, and another counts the notice's words."""
    for heading in re.finditer(r"<(h2|h3)\b[^>]*>(.*?)</\1>", INDEX, re.S):
        assert "info-btn" not in heading.group(2), heading.group(0)
    for notice in re.finditer(
            r"<p\b[^>]*class=\"privacy-notice\"[^>]*>(.*?)</p>", INDEX, re.S):
        assert "info-btn" not in notice.group(1), notice.group(0)
        assert "info-body" not in notice.group(1), notice.group(0)


def test_the_generated_notes_come_from_one_helper() -> None:
    """app.js builds the same markup rather than a second spelling of it."""
    assert "function infoNote(" in APP_JS
    assert APP_JS.count('class="info-btn"') == 1
    assert APP_JS.count('class="info-body hidden"') == 1
    # One delegated listener, so a note rendered later needs no re-binding.
    assert 'document.addEventListener("click"' in APP_JS
    assert 'document.addEventListener("keydown"' in APP_JS


def test_the_control_is_drawn_in_css_with_no_icon_font_or_animation() -> None:
    css = (STATIC / "style.css").read_text()
    block = css.split(".info-btn > span[aria-hidden=\"true\"] {", 1)[1].split("}", 1)[0]
    assert "border-radius: 50%" in block
    assert "border: 1px solid currentColor" in block
    rule = css.split("\n.info-btn {", 1)[1].split("\n}", 1)[0]
    assert "width: 24px" in rule and "height: 24px" in rule
    for forbidden in ("animation", "transition", "@font-face", "background-image"):
        assert forbidden not in css, forbidden
