"""Both consensus places are in every response and in both exports.

Applying the 161 km rule to the exported consensus columns reproduces the
flag; applying it to the fused columns does not.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/geolens/ui/static"
INDEX = (STATIC / "index.html").read_text()
APP_JS = (STATIC / "app.js").read_text()


def test_the_per_row_csv_carries_the_consensus_cities() -> None:
    assert '"post_consensus_city", "user_consensus_city"' in APP_JS
    assert "csvCell(tri?.post_consensus_city" in APP_JS
    assert "csvCell(tri?.user_consensus_city" in APP_JS


def test_the_geojson_carries_the_consensus_cities() -> None:
    assert "post_consensus_city: r.triangulation?.post_consensus_city" in APP_JS
    assert "user_consensus_city: r.triangulation?.user_consensus_city" in APP_JS


def test_the_flag_line_joins_the_consensus_cities() -> None:
    assert "consensusPoints[0].coords, consensusPoints[1].coords" in APP_JS
    # `place` is `bucketMarker` plus the offset that keeps two markers at one
    # coordinate from merging into a single blob.
    assert 'place(bucket, coords, popup, "consensus")' in APP_JS
    assert "function bucketMarker(" in APP_JS


def test_the_fused_panel_says_how_each_city_is_computed() -> None:
    assert "sum of each engine's top-k scores" in INDEX
    assert "top prediction weighted by its confidence" in INDEX
    assert "not calibrated" in INDEX


def test_the_flag_note_says_the_fusion_method_does_not_affect_it() -> None:
    assert "does not depend" in INDEX
    assert "fusion method selected" in INDEX


def test_prediction_error_is_the_first_candidate_cause() -> None:
    causes = INDEX.split("<summary>What the candidate causes mean")[1]
    first = causes.split("<li>")[1]
    assert "One of the two predictions is wrong" in first


def test_a_near_miss_is_reported_on_screen() -> None:
    assert 'id="near-miss-note"' in INDEX
    assert "No verification flag: " in APP_JS


def test_the_flag_caveat_keeps_its_second_clause_behind_the_icon() -> None:
    """The caveat is one line; what most often causes a flag is one click on."""
    assert "A prompt for review, not a finding." in INDEX
    body = INDEX.split('id="i-flag-caveat"', 1)[1].split("</div>", 1)[0]
    assert "Most often one of the two predictions is wrong." in body
