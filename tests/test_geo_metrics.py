"""Tests for the spatial-distance evaluation metrics."""

from __future__ import annotations

from geolens.batch.metrics import compute_summary
from geolens.batch.runner import BatchRow
from geolens.engines.base import Prediction
from geolens.geo import ACC_KM_THRESHOLD, haversine_km


def test_haversine_known_distances():
    kl = (3.139, 101.6869)
    pj = (3.1073, 101.6067)
    sg = (1.3521, 103.8198)
    tokyo = (35.6762, 139.6503)
    # Kuala Lumpur <-> Petaling Jaya is a near-miss (~9-12 km).
    assert haversine_km(kl, pj) < 20
    # Singapore <-> Tokyo is a far-miss (~5300 km).
    assert 5000 < haversine_km(sg, tokyo) < 5600
    # Symmetry and identity.
    assert haversine_km(kl, kl) == 0.0
    assert abs(haversine_km(kl, sg) - haversine_km(sg, kl)) < 1e-6


def _row(rid, gt, pred_city):
    pred = Prediction(city=pred_city, confidence=0.9, top_k=[(pred_city, 0.9)])
    return BatchRow(
        id=rid, status="ok", in_catalogue=True, ground_truth_city=gt,
        per_engine={"e": pred},
    )


def test_acc_at_161km_separates_near_and_far():
    # Truth is Kuala Lumpur. One prediction is a near-miss (Petaling Jaya),
    # one is a far-miss (Tokyo). Both are wrong on Acc@1, but Acc@161km
    # should credit only the near-miss.
    rows = [_row("1", "Kuala Lumpur", "Petaling Jaya"), _row("2", "Kuala Lumpur", "Tokyo")]
    s = compute_summary(rows)
    m = s.per_engine["e"]
    assert m.acc_at_1 == 0.0  # neither is exactly right
    assert m.acc_at_161km == 0.5  # only the near-miss is within 100 miles
    assert m.n_geo == 2
    assert m.median_error_km > 0


def test_exact_hit_is_zero_km():
    rows = [_row("1", "Singapore", "Singapore")]
    s = compute_summary(rows)
    m = s.per_engine["e"]
    assert m.acc_at_1 == 1.0
    assert m.median_error_km == 0.0
    assert m.acc_at_161km == 1.0


def test_unknown_coord_prediction_counts_as_outside_threshold():
    rows = [_row("1", "Singapore", "Atlantis")]  # predicted city has no coordinate
    s = compute_summary(rows)
    m = s.per_engine["e"]
    assert m.acc_at_161km == 0.0
    assert m.n_geo == 1  # ground truth has a coordinate, so the row still counts


def test_threshold_is_100_miles():
    assert abs(ACC_KM_THRESHOLD - 161.0) < 1.0
