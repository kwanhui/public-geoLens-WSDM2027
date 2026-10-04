from __future__ import annotations

from geolens.engines.base import Prediction
from geolens.ensemble import ensemble


def _pred(city, conf, top_k):
    return Prediction(city=city, confidence=conf, top_k=top_k)


def test_weighted_can_be_dominated_by_large_scores():
    # Engine A is confidently wrong with a huge raw score; B and C agree on the
    # right answer with small scores. A weighted sum follows the large score.
    per_engine = {
        "a": _pred("Tokyo", 9.0, [("Tokyo", 9.0), ("Singapore", 0.1)]),
        "b": _pred("Singapore", 0.3, [("Singapore", 0.3), ("Tokyo", 0.1)]),
        "c": _pred("Singapore", 0.2, [("Singapore", 0.2), ("Tokyo", 0.1)]),
    }
    grans = {"a": "post", "b": "post", "c": "post"}
    w = ensemble(per_engine, grans, target="post", method="weighted")
    r = ensemble(per_engine, grans, target="post", method="rrf")
    assert w.method == "weighted" and r.method == "rrf"
    # Weighted is dragged to Tokyo by A's inflated score...
    assert w.consensus_city == "Tokyo"
    # ...but RRF, using only rank, follows the 2-of-3 majority for Singapore.
    assert r.consensus_city == "Singapore"


def test_rrf_uses_rank_not_magnitude():
    # Same ranking, wildly different score magnitudes -> identical RRF result.
    small = {"a": _pred("Bangkok", 0.01, [("Bangkok", 0.01), ("Manila", 0.001)])}
    big = {"a": _pred("Bangkok", 999.0, [("Bangkok", 999.0), ("Manila", 1.0)])}
    grans = {"a": "post"}
    rs = ensemble(small, grans, target="post", method="rrf")
    rb = ensemble(big, grans, target="post", method="rrf")
    assert rs.consensus_city == rb.consensus_city == "Bangkok"
    assert abs(rs.consensus_confidence - rb.consensus_confidence) < 1e-9


def test_default_method_is_weighted():
    per_engine = {"a": _pred("Seoul", 0.5, [("Seoul", 0.5)])}
    r = ensemble(per_engine, {"a": "post"}, target="post")
    assert r.method == "weighted"
