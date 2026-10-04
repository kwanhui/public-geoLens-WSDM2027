"""Combine per-engine predictions into one consensus per level.

One set of per-engine predictions yields the single best place across engines
weighted by per-engine confidence, an agreement score in [0, 1] that is high
when the engines vote the same way, and the verification flag, raised when the
post-level and user-level consensus are more than ``flag_radius_km`` apart.

The flag compares each level's consensus rather than the raw per-engine label
sets, and gates on great-circle distance, so a same-metro pair is not treated
like Singapore and Tokyo.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
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
    # How many of the engines that answered at each level named that level's
    # consensus place, out of how many answered. The vote is not part of the
    # rule; it says how thin the majority behind a flagged pair was.
    post_consensus_votes: int = 0
    post_consensus_answered: int = 0
    user_consensus_votes: int = 0
    user_consensus_answered: int = 0
    per_engine: dict[str, Prediction] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _vote_top1(per_engine: dict[str, Prediction]) -> tuple[str, float]:
    """Confidence-weighted top-1 vote over the engines that named a city."""
    bucket: dict[str, float] = defaultdict(float)
    for pred in per_engine.values():
        if not pred.city:
            continue
        bucket[pred.city] += pred.confidence
    if not bucket:
        return "", 0.0
    best = max(bucket.items(), key=lambda x: x[1])
    total = sum(bucket.values()) or 1.0
    return best[0], best[1] / total


def _agreement(per_engine: dict[str, Prediction]) -> float:
    """Fraction of the engines that named a city whose top-1 is the consensus.

    An engine that abstained named nothing, so counting it in the denominator
    would report disagreement it did not express.
    """
    answered = [p for p in per_engine.values() if p.city]
    if not answered:
        return 0.0
    consensus, _ = _vote_top1(per_engine)
    if not consensus:
        return 0.0
    return sum(1 for p in answered if p.city == consensus) / len(answered)


def _bucket_consensus(
    per_engine: dict[str, Prediction],
    engines: Mapping[str, str],
    granularity: str,
) -> str | None:
    """Confidence-weighted top-1 city among engines of one granularity."""
    bucket = {n: p for n, p in per_engine.items() if engines.get(n) == granularity}
    if not bucket:
        return None
    return _vote_top1(bucket)[0] or None


def _bucket_vote(
    per_engine: dict[str, Prediction],
    engines: Mapping[str, str],
    granularity: str,
    city: str,
) -> tuple[int, int]:
    """How many engines at one level put `city` first, out of those that answered."""
    answered = [
        p for n, p in per_engine.items() if engines.get(n) == granularity and p.city
    ]
    return sum(1 for p in answered if p.city == city), len(answered)


def triangulate(
    per_engine: dict[str, Prediction],
    engines: Mapping[str, str] | None = None,
    radius_km: float = ACC_KM_THRESHOLD,
) -> TriangulationResult:
    """Reduce a dict of {engine_name: Prediction} to a single TriangulationResult.

    `engines` maps engine_name -> granularity ("post" | "user"); used to detect
    post-vs-user disagreement (the OSINT signal).

    `radius_km` is the separation at which the flag is raised. It defaults to
    the 161 km (100 mile) convention every reported number was produced under.
    """

    # A call that failed carries no city, so it takes no part in any vote and
    # cannot raise or suppress the flag. The result still reports it, and the
    # caller still sees it on the engine card.
    usable = {n: p for n, p in per_engine.items() if p.usable}
    if not usable:
        return TriangulationResult(
            consensus_city="",
            consensus_confidence=0.0,
            agreement_score=0.0,
            disagreement_flag=False,
            per_engine=per_engine,
            notes=["no engine returned a prediction"],
        )

    consensus_city, consensus_conf = _vote_top1(usable)
    agreement = _agreement(usable)

    notes: list[str] = []
    disagreement = False
    post_city = user_city = ""
    distance_km: float | None = None
    score = 0.0
    post_votes = post_answered = user_votes = user_answered = 0

    if engines:
        post_city = _bucket_consensus(usable, engines, "post") or ""
        user_city = _bucket_consensus(usable, engines, "user") or ""
        post_votes, post_answered = _bucket_vote(usable, engines, "post", post_city)
        user_votes, user_answered = _bucket_vote(usable, engines, "user", user_city)
        if post_city and user_city and post_city != user_city:
            cp, cu = coords_for(post_city), coords_for(user_city)
            if cp is not None and cu is not None:
                distance_km = haversine_km(cp, cu)
                score = min(1.0, distance_km / DISAGREEMENT_SCALE_KM)
                if distance_km > radius_km:
                    disagreement = True
                    notes.append(
                        f"the post text points to {post_city} and the account's "
                        f"timeline points to {user_city}, {distance_km:,.0f} km apart. "
                        "This is a prompt to review the case, not a finding. "
                        "The candidate causes are that one of the two predictions "
                        "is wrong, which is the most common one, that the post is "
                        "about another place, a travel post, a shared or compromised "
                        "account, and a misleading geotag."
                    )
                else:
                    notes.append(
                        f"the post text points to {post_city} and the account's "
                        f"timeline points to {user_city}, but they are only "
                        f"{distance_km:,.0f} km apart, which is inside the "
                        f"{radius_km:,.0f} km radius. Within one metropolitan area this is "
                        "a near miss rather than a conflict, so no flag is raised."
                    )
            else:
                # One side has no coordinate (e.g. a just-onboarded city without
                # a centroid): flag it but mark the distance as unknown.
                disagreement = True
                score = 0.5
                notes.append(
                    f"the post text points to {post_city} and the account's timeline "
                    f"points to {user_city}, and the distance between them "
                    "is unknown because one of the two has no coordinate. "
                    "Review the case by hand."
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
        post_consensus_votes=post_votes,
        post_consensus_answered=post_answered,
        user_consensus_votes=user_votes,
        user_consensus_answered=user_answered,
        per_engine=per_engine,
        notes=notes,
    )
