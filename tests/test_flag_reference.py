"""The flag's measured error rate, as the interface prints it.

The rates come from the committed rescore of the WNUT-2016 run. A packaged
copy exists because the evaluation directory is not part of the deployed
image.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from geolens.flag_reference import DATA_FILE, REPORTED_ROW_SET, flag_reference

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "eval/results/wnut2016_flag_rescore.json"


def test_the_packaged_copy_matches_the_committed_rescore() -> None:
    assert DATA_FILE.read_bytes() == COMMITTED.read_bytes()


def test_the_rates_are_read_from_the_rescore_not_written_here() -> None:
    row = next(
        r
        for r in json.loads(COMMITTED.read_text())["row_sets"]
        if r["row_set"] == REPORTED_ROW_SET
    )
    ref = flag_reference()

    negatives = row["false_positive"] + row["true_negative"]
    assert ref["false_alarm_rate"] == row["false_positive"] / negatives
    assert ref["precision"] == row["precision"]
    assert ref["n_rows"] == row["n_rows"]


def test_the_sentence_says_what_the_flag_measured() -> None:
    sentence = flag_reference()["sentence"]
    assert sentence == (
        "On WNUT-2016, 60% of accounts whose post and home agreed were also "
        "flagged; treat a flag as a reason to look."
    )


def test_the_headline_percentage_matches_the_sentence() -> None:
    """The page prints the rate on its own line and the sentence behind an icon.

    It rounds ``false_alarm_rate`` itself, so the two have to agree.
    """
    reference = flag_reference()
    percent = math.floor(reference["false_alarm_rate"] * 100 + 0.5)
    assert f"{percent}%" in reference["sentence"]


def test_the_page_can_read_the_sentence_from_the_instance_endpoint(monkeypatch, tmp_path) -> None:
    from starlette.testclient import TestClient

    from geolens.ui.server import create_app

    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as client:
        body = client.get("/instance").json()
    assert body["flag_reference"]["sentence"] == flag_reference()["sentence"]
