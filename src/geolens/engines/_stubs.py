"""Deterministic stub predictions used when GEOLENS_STUB_MODE=1.

Stub output is seeded by (engine_name, input_text) so the same input always
returns the same prediction. We add small, engine-specific perturbations so
the triangulator surfaces realistic agreement and disagreement cases instead
of three identical answers.
"""

from __future__ import annotations

import hashlib
import math
import time

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines.base import GeolocateInput, Prediction


def _seed(engine_name: str, payload: GeolocateInput) -> int:
    raw = (
        engine_name
        + "::"
        + (payload.post or "")
        + "::"
        + (payload.user_handle or "")
        + "::"
        + " ".join(payload.user_posts or [])
    )
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)


def _softmax(scores: list[float]) -> list[float]:
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    z = sum(exps)
    return [e / z for e in exps]


def stub_predict(
    engine_name: str,
    payload: GeolocateInput,
    k: int,
    *,
    cities: list[str] | None = None,
    sleep_ms: float = 40.0,
    cost_usd: float = 0.0,
    note: str = "stub mode",
) -> Prediction:
    """Generate a deterministic top-k for one engine."""

    cities = cities or DEFAULT_CITIES
    seed = _seed(engine_name, payload)
    start = time.perf_counter()

    # Score each city by a hash-derived value, then softmax.
    raw_scores = [
        ((seed * (i + 1) * 2654435761) & 0xFFFFFFFF) / 0xFFFFFFFF
        for i in range(len(cities))
    ]
    probs = _softmax([s * 6 for s in raw_scores])
    ranked = sorted(zip(cities, probs), key=lambda x: x[1], reverse=True)
    top = ranked[:k]

    # Cheap delay so the UI can show realistic timings without burning CPU.
    time.sleep(sleep_ms / 1000)
    latency_ms = (time.perf_counter() - start) * 1000

    return Prediction(
        city=top[0][0],
        confidence=float(top[0][1]),
        top_k=[(c, float(p)) for c, p in top],
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        note=note,
    )
