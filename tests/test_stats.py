"""Tests for Wilson intervals and the catalogue-size surfacing."""

from __future__ import annotations

from geolens.batch.metrics import compute_summary
from geolens.batch.runner import BatchRow
from geolens.engines.base import Prediction
from geolens.stats import wilson_interval


def test_wilson_brackets_point_estimate():
    low, high = wilson_interval(45, 48)  # ~0.94
    assert 0.0 <= low < 45 / 48 < high <= 1.0
    # Small-n interval should be visibly wide.
    assert (high - low) > 0.08


def test_wilson_edges_are_clamped():
    low, high = wilson_interval(48, 48)  # perfect score
    assert high == 1.0 or abs(high - 1.0) < 1e-9  # upper bound reaches 1.0 (modulo FP)
    assert low < 0.95  # at n=48 a perfect score still has a non-trivial lower bound
    low0, high0 = wilson_interval(0, 48)
    assert low0 == 0.0 and high0 > 0.0


def test_wilson_zero_n():
    assert wilson_interval(0, 0) == (0.0, 0.0)


def _row(rid, gt, pred_city):
    pred = Prediction(city=pred_city, confidence=0.9, top_k=[(pred_city, 0.9)])
    return BatchRow(id=rid, status="ok", in_catalogue=True,
                    ground_truth_city=gt, per_engine={"e": pred})


def test_summary_reports_ci_and_catalogue_size():
    rows = [_row("1", "Singapore", "Singapore"), _row("2", "Tokyo", "Tokyo"),
            _row("3", "London", "Bangkok")]
    s = compute_summary(rows, catalogue_size=22)
    assert s.catalogue_size == 22
    m = s.per_engine["e"]
    assert m.acc_at_1_ci[0] <= m.acc_at_1 <= m.acc_at_1_ci[1]
    assert m.acc_at_1_ci[0] < m.acc_at_1_ci[1]  # an actual interval, not a point
