"""Combine per-engine predictions into a consensus verdict.

Triangulation surfaces three things the UI cares about:
1. The single best city across engines (weighted by per-engine confidence).
2. An agreement score in [0, 1], high when engines vote the same way.
3. A cross-task signal when the post-level consensus and the user-level
   consensus point at different places (the OSINT inconsistency signal).

The cross-task signal is distance-aware. Comparing the *consensus* of the
post bucket against the *consensus* of the user bucket (rather than raw
per-engine label sets) keeps the banner aligned with the verdict cards the
operator actually reads, and gating on great-circle distance stops a
same-metro near-miss (e.g. Singapore vs. an adjacent town) from firing the
same alarm as a cross-continent conflict (Singapore vs. Tokyo).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from geolens.engines._coords import coords_for
from geolens.engines.base import Prediction
from geolens.geo import ACC_KM_THRESHOLD, haversine_km

# A cross-task split this far apart (km) is treated as a maximal-strength
# signal; the score scales linearly up to it.
DISAGREEMENT_SCALE_KM = 2000.0


@dataclass
class TriangulationResult:
    consensus_city: str
    consensus_confidence: float
    agreement_score: float
    disagreement_flag: bool
    post_consensus_city: str = ""
    user_consensus_city: str = ""
    disagreement_km: float | None = None
    disagreement_score: float = 0.0
    per_engine: dict[str, Prediction] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _vote_top1(per_engine: dict[str, Prediction]) -> tuple[str, float]:
    """Confidence-weighted top-1 vote."""
    bucket: dict[str, float] = defaultdict(float)
    for pred in per_engine.values():
        bucket[pred.city] += pred.confidence
    best = max(bucket.items(), key=lambda x: x[1])
    total = sum(bucket.values()) or 1.0
    return best[0], best[1] / total


def _agreement(per_engine: dict[str, Prediction]) -> float:
    """Fraction of engines whose top-1 matches the consensus."""
    if not per_engine:
        return 0.0
    consensus, _ = _vote_top1(per_engine)
    matches = sum(1 for p in per_engine.values() if p.city == consensus)
    return matches / len(per_engine)


def _bucket_consensus(
    per_engine: dict[str, Prediction],
    engines: dict[str, str],
    granularity: str,
) -> str | None:
    """Confidence-weighted top-1 city among engines of one granularity."""
    bucket = {n: p for n, p in per_engine.items() if engines.get(n) == granularity}
    if not bucket:
        return None
    return _vote_top1(bucket)[0]


def triangulate(
    per_engine: dict[str, Prediction],
    engines: dict[str, str] | None = None,
) -> TriangulationResult:
    """Reduce a dict of {engine_name: Prediction} to a single TriangulationResult.

    `engines` maps engine_name -> granularity ("post" | "user"); used to detect
    post-vs-user disagreement (the OSINT signal).
    """

    if not per_engine:
        return TriangulationResult(
            consensus_city="",
            consensus_confidence=0.0,
            agreement_score=0.0,
            disagreement_flag=False,
            notes=["no engines returned a prediction"],
        )

    consensus_city, consensus_conf = _vote_top1(per_engine)
    agreement = _agreement(per_engine)

    notes: list[str] = []
    disagreement = False
    post_city = user_city = ""
    distance_km: float | None = None
    score = 0.0

    if engines:
        post_city = _bucket_consensus(per_engine, engines, "post") or ""
        user_city = _bucket_consensus(per_engine, engines, "user") or ""
        if post_city and user_city and post_city != user_city:
            cp, cu = coords_for(post_city), coords_for(user_city)
            if cp is not None and cu is not None:
                distance_km = haversine_km(cp, cu)
                score = min(1.0, distance_km / DISAGREEMENT_SCALE_KM)
                if distance_km > ACC_KM_THRESHOLD:
                    disagreement = True
                    notes.append(
                        f"post-level consensus ({post_city}) and user-level "
                        f"consensus ({user_city}) are {distance_km:,.0f} km apart; "
                        "consider a travel post, a shared or compromised "
                        "account, or a misleading geotag"
                    )
                else:
                    notes.append(
                        f"post-level ({post_city}) and user-level ({user_city}) "
                        f"consensus differ but are only {distance_km:,.0f} km apart "
                        "(same metro), treated as a near-miss, not a conflict"
                    )
            else:
                # One side has no coordinate (e.g. a just-onboarded city without
                # a centroid): flag it but mark the distance as unknown.
                disagreement = True
                score = 0.5
                notes.append(
                    f"post-level consensus ({post_city}) and user-level "
                    f"consensus ({user_city}) differ; distance unknown "
                    "(missing coordinate); review manually"
                )

    return TriangulationResult(
        consensus_city=consensus_city,
        consensus_confidence=consensus_conf,
        agreement_score=agreement,
        disagreement_flag=disagreement,
        post_consensus_city=post_city,
        user_consensus_city=user_city,
        disagreement_km=distance_km,
        disagreement_score=score,
        per_engine=per_engine,
        notes=notes,
    )
