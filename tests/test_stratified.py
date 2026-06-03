"""Tests for per-difficulty stratified metrics."""

from __future__ import annotations

from geolens.batch.metrics import bucket_of, compute_summary
from geolens.batch.runner import BatchRow
from geolens.engines.base import Prediction


def _row(rid, gt, pred_city, bucket=None):
    pred = Prediction(city=pred_city, confidence=0.9, top_k=[(pred_city, 0.9)])
    return BatchRow(id=rid, status="ok", in_catalogue=True,
                    ground_truth_city=gt, bucket=bucket, per_engine={"e": pred})


def test_bucket_derived_from_id_prefix():
    assert bucket_of(_row("hard-sem-3", "X", "X")) == "hard-sem"
    assert bucket_of(_row("sg-explicit-1", "X", "X")) == "sg-explicit"
    assert bucket_of(_row("ambig-3", "X", "X")) == "ambig"
    # Explicit bucket wins over the id prefix.
    assert bucket_of(_row("sg-explicit-1", "X", "X", bucket="manual")) == "manual"


def test_per_bucket_acc_separates_buckets():
    rows = [
        # explicit-mention bucket: engine gets both right
        _row("sg-explicit-1", "Singapore", "Singapore"),
        _row("sg-explicit-2", "Tokyo", "Tokyo"),
        # hard-semantic bucket: engine gets both wrong
        _row("hard-sem-1", "Singapore", "London"),
        _row("hard-sem-2", "Singapore", "Bangkok"),
    ]
    s = compute_summary(rows)
    assert set(s.per_bucket) == {"sg-explicit", "hard-sem"}
    assert s.per_bucket["sg-explicit"].acc_at_1["e"] == 1.0
    assert s.per_bucket["hard-sem"].acc_at_1["e"] == 0.0
    assert s.per_bucket["sg-explicit"].n_rows == 2
