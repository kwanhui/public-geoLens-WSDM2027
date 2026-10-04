"""Metrics for an engine that abstained rather than answered.

An abstention is left out of the distance metrics and counted separately.
`mean_rank` keeps the 1,000,000 sentinel for compatibility; `mean_rank_found`
averages only the rows where the truth was in the list.
"""

from __future__ import annotations

import pytest

from geolens.batch import BatchInput, run_batch
from geolens.batch.metrics import compute_summary
from geolens.engines.registry import build_engines

PLACELESS = [
    "The lift is broken again and nobody has come to fix it",
    "Rat in the void deck, third one this week",
]


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _summary():
    engines, catalogue = build_engines()
    rows = [
        BatchInput(id=f"p{i}", post=text, ground_truth_city="Bedok")
        for i, text in enumerate(PLACELESS)
    ]
    return compute_summary(
        run_batch(rows, engines, catalogue=catalogue, k=3),
        catalogue_size=len(catalogue),
        granularities={n: e.granularity for n, e in engines.items()},
    )


def test_an_abstention_is_counted_not_scored_as_a_near_miss() -> None:
    m = _summary().per_engine["gazetteer_post"]

    assert m.n_evaluated == len(PLACELESS)
    assert m.n_abstained == len(PLACELESS)
    assert m.n_geo == 0
    assert m.median_error_km == 0.0  # no rows contributed; n_geo says so


def test_the_mean_rank_over_found_rows_excludes_the_sentinel() -> None:
    m = _summary().per_engine["gazetteer_post"]

    assert m.mean_rank > 1000  # the raw mean still averages the sentinel
    assert m.n_rank_found == 0
    assert m.mean_rank_found == 0.0


def test_an_engine_that_answers_reports_the_rows_it_was_found_on() -> None:
    engines, catalogue = build_engines()
    rows = [
        BatchInput(id="1", post="Queue at the Bedok hawker centre", ground_truth_city="Bedok"),
        BatchInput(id="2", post="Nothing to see here at all", ground_truth_city="Bedok"),
    ]
    summary = compute_summary(
        run_batch(rows, engines, catalogue=catalogue, k=3),
        catalogue_size=len(catalogue),
        granularities={n: e.granularity for n, e in engines.items()},
    )
    m = summary.per_engine["gazetteer_post"]

    assert m.n_abstained == 1
    assert m.n_rank_found == 1
    assert m.mean_rank_found == 1.0
    assert m.n_geo == 1
