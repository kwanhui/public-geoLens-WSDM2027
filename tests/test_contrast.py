"""Text contrast ratios, computed from the stylesheet's custom properties."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[1]
       / "src/geolens/ui/static/style.css").read_text()

AA_TEXT = 4.5
AA_LARGE = 3.0


def custom_properties() -> dict[str, str]:
    block = CSS.split(":root {", 1)[1].split("\n}", 1)[0]
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", block))


PROPS = custom_properties()


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    hexed = colour.lstrip("#")
    if len(hexed) == 3:
        hexed = "".join(c * 2 for c in hexed)
    r, g, b = (int(hexed[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(foreground: str, background: str) -> float:
    a, b = luminance(foreground), luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def colour(name: str) -> str:
    assert name in PROPS, f"{name} is not defined on :root"
    return PROPS[name]


def test_the_formula_matches_the_wcag_reference_values() -> None:
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast("#98a2b3", "#ffffff"), 2) == 2.58
    assert round(contrast("#475467", "#142850"), 2) == 1.89
    assert round(contrast("#ffffff", "#9ca3af"), 2) == 2.54


# (what it is, foreground, background, the floor it has to clear)
PAIRS = [
    ("body text on a card", "--ink", "--surface", AA_TEXT),
    ("body text on the page", "--ink", "--page", AA_TEXT),
    ("secondary text on a card", "--ink-soft", "--surface", AA_TEXT),
    ("secondary text on the page", "--ink-soft", "--page", AA_TEXT),
    ("the smallest helper text on a card", "--ink-faint", "--surface", AA_TEXT),
    ("the confidence intervals and the run manifest", "--ink-faint", "--page", AA_TEXT),
    ("a link in running text", "--accent", "--surface", AA_TEXT),
    ("a link on the page background", "--accent", "--page", AA_TEXT),
    ("the primary button's label", "--surface", "--accent", AA_TEXT),
    ("the disabled button's label", "--surface", "--ink-faint", AA_TEXT),
    ("the header subtitle", "--on-navy-soft", "--navy", AA_TEXT),
    ("the quota line on the hosted header", "--on-navy-soft", "--navy", AA_TEXT),
    ("a best cell in the summary table", "--ok", "--surface", AA_TEXT),
    ("an out-of-catalogue row", "--warn", "--surface", AA_TEXT),
    ("a warning box", "--warn", "--warn-wash", AA_TEXT),
    ("an error cell", "--danger", "--surface", AA_TEXT),
    ("the verification flag's text", "--flag-ink", "--flag-wash", AA_TEXT),
    ("the flag's border against the page", "--flag-rule", "--page", AA_LARGE),
    ("a field border against a card", "--rule-strong", "--surface", AA_LARGE),
    ("the filled part of the scenario progress bar", "--accent", "--wash", AA_LARGE),
]


@pytest.mark.parametrize(
    ("what", "fg", "bg", "floor"), PAIRS, ids=[p[0] for p in PAIRS])
def test_the_pair_clears_its_floor(what: str, fg: str, bg: str, floor: float) -> None:
    ratio = contrast(colour(fg), colour(bg))
    assert ratio >= floor, f"{what}: {colour(fg)} on {colour(bg)} is {ratio:.2f}:1"


# Colours that fall below 4.5:1 against the surface they were used on.
BELOW_FLOOR = ["#98a2b3", "#9ca3af", "#059669", "#d97706", "#1d4ed8"]


@pytest.mark.parametrize("value", BELOW_FLOOR)
def test_a_colour_below_the_floor_is_absent(value: str) -> None:
    assert value not in CSS.lower(), f"{value} measures below 4.5:1"


def test_the_selected_tile_keeps_its_secondary_line_legible() -> None:
    """White at 75% opacity on the accent computes to 4.48:1, so it is set."""
    assert "opacity: .75" not in CSS and "opacity:.75" not in CSS
    block = CSS.split('.scenario-tile[aria-pressed="true"] .tile-subtitle {', 1)[1]
    match = re.search(r"color:\s*(#[0-9a-fA-F]{6})", block)
    assert match
    assert contrast(match.group(1), colour("--accent")) >= AA_TEXT


def test_a_ranked_cell_is_not_marked_by_colour_alone() -> None:
    """A ranked cell carries a symbol and a title, not colour alone."""
    app_js = (Path(__file__).resolve().parents[1]
              / "src/geolens/ui/static/app.js").read_text()
    assert '<span class="rank-mark">${rank}</span>' in app_js
    assert '? "best" : (m.acc_at_1 === worstAcc1 ? "worst" : "")' in app_js
    assert ".rank-mark {" in CSS


def test_links_in_running_text_are_underlined() -> None:
    """The accent against the body ink is 1.4:1, below G183's 3:1, so a link
    in running text is underlined as well as coloured."""
    block = CSS.split("p a, li a, td a, .batch-intro a {", 1)[1].split("}", 1)[0]
    assert "text-decoration: underline" in block
