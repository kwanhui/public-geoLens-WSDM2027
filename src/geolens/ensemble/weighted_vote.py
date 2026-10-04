"""Granularity-aware fusion across engines.

Within each granularity bucket, post-level and user-level, the contributing
engines' top-k predictions are fused. Nothing is fused across granularities,
because that comparison is the cross-task verification the flag performs.

Two methods are offered. ``weighted`` sums each engine's top-k probability
mass, with uniform weights by default. ``rrf`` is Reciprocal Rank Fusion
(Cormack et al., SIGIR 2009), which uses only rank position, so the
incomparable score scales of a substring count, a self-reported LLM confidence
and a softmaxed cosine similarity do not distort the result. That is why it is
the default.

An EnsembleResult carries the fused ranking and the difference against the best
single engine in the bucket.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from geolens.engines.base import Prediction

Granularity = Literal["post", "user"]
FusionMethod = Literal["weighted", "rrf"]

# Conventional RRF damping constant (Cormack et al. 2009 use 60).
RRF_K = 60


def as_fusion_method(value: str) -> FusionMethod:
    """Narrow a caller-supplied string to a supported fusion method.

    Requests and CLI flags carry a plain string; anything other than ``rrf``
    runs the weighted sum, which is what `ensemble` has always done.
    """
    return "rrf" if value == "rrf" else "weighted"


@dataclass
class EnsembleResult:
    granularity: Granularity
    consensus_city: str
    consensus_confidence: float
    method: FusionMethod = "weighted"
    top_k: list[tuple[str, float]] = field(default_factory=list)
    contributing_engines: list[str] = field(default_factory=list)
    # Best single engine in this bucket, for the delta calculation.
    best_single_engine: str = ""
    best_single_city: str = ""
    best_single_confidence: float = 0.0
    # Positive delta = ensemble more confident than best single. Negative
    # means the best single was more confident; whether that's better
    # depends on accuracy, which the eval table covers.
    delta_vs_best_single: float = 0.0
    # True iff the ensemble's top-1 differs from the best single's top-1.
    differs_from_best_single: bool = False


def _fuse_weighted(
    contributing: list[tuple[str, Prediction]], weights: dict[str, float]
) -> dict[str, float]:
    """Sum each engine's top-k probability mass (uniform weights by default)."""
    bucket: dict[str, float] = defaultdict(float)
    for name, pred in contributing:
        w = weights.get(name, 1.0)
        for city, prob in pred.top_k:
            bucket[city] += w * prob
    return bucket


def _fuse_rrf(
    contributing: list[tuple[str, Prediction]], weights: dict[str, float]
) -> dict[str, float]:
    """Reciprocal Rank Fusion: score(c) = sum_e w_e / (RRF_K + rank_e(c)).

    Only the rank of a city within each engine's top-k matters, so engines
    with incomparable score scales contribute on equal footing.
    """
    bucket: dict[str, float] = defaultdict(float)
    for name, pred in contributing:
        w = weights.get(name, 1.0)
        for rank, (city, _prob) in enumerate(pred.top_k, start=1):
            bucket[city] += w / (RRF_K + rank)
    return bucket


def ensemble(
    per_engine: dict[str, Prediction],
    granularities: dict[str, Granularity],
    target: Granularity,
    weights: dict[str, float] | None = None,
    k: int = 5,
    method: FusionMethod = "weighted",
) -> EnsembleResult | None:
    """Ensemble the engines whose granularity == `target` using `method`.

    Returns None if no engine in the bucket produced a usable prediction.
    """

    weights = weights or {}
    # A failed call carries no top-k, so it cannot contribute a city. The
    # `usable` test says so explicitly rather than relying on that.
    contributing: list[tuple[str, Prediction]] = [
        (n, p)
        for n, p in per_engine.items()
        if granularities.get(n) == target and p.usable and p.top_k
    ]
    if not contributing:
        return None

    bucket = _fuse_rrf(contributing, weights) if method == "rrf" else _fuse_weighted(contributing, weights)

    # Normalise so the consensus reads as a distribution over candidates.
    # (RRF scores are not probabilities; normalising only makes them
    # comparable within this one result for display.)
    total = sum(bucket.values()) or 1.0
    ranked = sorted(((c, s / total) for c, s in bucket.items()), key=lambda x: x[1], reverse=True)
    top_k = ranked[: max(1, k)]

    # Best single engine in the bucket = highest top-1 confidence.
    best_name, best_pred = max(contributing, key=lambda np: np[1].confidence)
    delta = top_k[0][1] - best_pred.confidence

    return EnsembleResult(
        granularity=target,
        consensus_city=top_k[0][0],
        consensus_confidence=float(top_k[0][1]),
        method=method,
        top_k=[(c, float(p)) for c, p in top_k],
        contributing_engines=[n for n, _ in contributing],
        best_single_engine=best_name,
        best_single_city=best_pred.city,
        best_single_confidence=float(best_pred.confidence),
        delta_vs_best_single=float(delta),
        differs_from_best_single=top_k[0][0] != best_pred.city,
    )
