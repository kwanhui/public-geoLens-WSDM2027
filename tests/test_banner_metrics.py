"""Disagreement-banner precision/recall and catalogue-size reporting."""

from __future__ import annotations

from geolens.batch.metrics import compute_summary
from geolens.batch.runner import BatchRow
from geolens.triangulator import TriangulationResult


def _row(rid: str, gold: bool, fired: bool) -> BatchRow:
    tri = TriangulationResult(
        consensus_city="Singapore",
        consensus_confidence=0.9,
        agreement_score=1.0,
        disagreement_flag=fired,
    )
    return BatchRow(
        id=rid,
        status="ok",
        in_catalogue=True,
        ground_truth_city="Singapore",
        should_disagree=gold,
        per_engine={},  # not needed for banner metric
        triangulation=tri,
    )


def test_banner_precision_recall() -> None:
    rows = [
        _row("a", gold=True, fired=True),    # TP
        _row("b", gold=True, fired=False),   # FN
        _row("c", gold=False, fired=True),   # FP
        _row("d", gold=False, fired=False),  # TN
    ]
    s = compute_summary(rows, catalogue_size=22)
    assert s.banner is not None
    b = s.banner
    assert (b.n_labelled, b.n_positive) == (4, 2)
    assert (b.true_positive, b.false_positive, b.false_negative) == (1, 1, 1)
    assert b.precision == 0.5
    assert b.recall == 0.5


def test_no_banner_without_labels() -> None:
    rows = [_row("a", gold=None, fired=True)]  # type: ignore[arg-type]
    rows[0].should_disagree = None
    s = compute_summary(rows, catalogue_size=22)
    assert s.banner is None


def test_catalogue_size_is_reported() -> None:
    s = compute_summary([_row("a", gold=True, fired=True)], catalogue_size=23)
    assert s.catalogue_size == 23


def test_city_rollup_counts_per_bucket() -> None:
    from geolens.batch.metrics import compute_rollup
    from geolens.ensemble import EnsembleResult

    def _ens(city):
        return EnsembleResult(
            granularity="post", consensus_city=city, consensus_confidence=0.9,
            method="weighted", top_k=[(city, 0.9)], contributing_engines=["x"],
            best_single_engine="x", best_single_city=city, best_single_confidence=0.9,
            delta_vs_best_single=0.0, differs_from_best_single=False,
        )

    rows = [
        BatchRow(id="1", status="ok", in_catalogue=True, ground_truth_city=None,
                 ensembles={"post": _ens("Tokyo"), "user": _ens("Tokyo")}),
        BatchRow(id="2", status="ok", in_catalogue=True, ground_truth_city=None,
                 ensembles={"post": _ens("Tokyo")}),
    ]
    rollup = compute_rollup(rows)
    top = rollup[0]
    assert top.city == "Tokyo"
    assert top.post_count == 2 and top.user_count == 1
