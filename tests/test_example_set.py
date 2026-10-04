"""The bundled 50-row example set, and the mix of rows it has to carry."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_COPY = ROOT / "eval/example_test_set.csv"
STATIC_COPY = ROOT / "src/geolens/ui/static/example_test_set.csv"

EXPECTED_BUCKETS = {
    "sg-explicit": 6, "sg-implicit": 4, "osint": 4, "intl": 6, "crisis": 4,
    "ooc": 2, "hard-sem": 7, "disagree": 4, "userhome": 4, "multilang": 4,
    "sarcasm": 2, "ambig": 3,
}


def _rows() -> list[dict[str, str]]:
    with EVAL_COPY.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_the_two_copies_are_identical() -> None:
    assert EVAL_COPY.read_text() == STATIC_COPY.read_text()


def test_the_set_is_fifty_rows_in_the_documented_buckets() -> None:
    rows = _rows()
    assert len(rows) == 50
    assert sum(EXPECTED_BUCKETS.values()) == 50
    assert Counter(r["id"].rsplit("-", 1)[0] for r in rows) == EXPECTED_BUCKETS


def test_every_row_parses_into_the_full_schema() -> None:
    for row in _rows():
        assert set(row) == {
            "id", "post", "user_posts", "ground_truth_city",
            "ground_truth_user_city", "should_disagree",
        }
        assert row["post"] or row["user_posts"], row["id"]


def test_the_flag_has_negative_rows_with_a_timeline() -> None:
    """Without these the bulk view can report no false alarm at all."""
    timeline = [r for r in _rows() if r["user_posts"]]
    negatives = [r for r in timeline if r["should_disagree"] == "0"]

    assert len(timeline) == 12
    assert len(negatives) == 4
    assert all(r["id"].startswith("userhome") for r in negatives)


def test_a_home_consistent_row_names_the_same_place_at_both_levels() -> None:
    for row in _rows():
        if not row["id"].startswith("userhome"):
            continue
        assert row["ground_truth_city"] == row["ground_truth_user_city"]
        assert row["should_disagree"] == "0"


def test_a_disagreement_row_names_two_different_places() -> None:
    for row in _rows():
        if row["should_disagree"] != "1":
            continue
        assert row["ground_truth_user_city"]
        assert row["ground_truth_city"] != row["ground_truth_user_city"], row["id"]


def test_no_row_carries_a_handle() -> None:
    assert "@" not in EVAL_COPY.read_text()
