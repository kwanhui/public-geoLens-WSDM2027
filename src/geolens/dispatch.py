"""Decide which engines to run for one input, and run them.

The single-query endpoint, the batch runner and the CLI all come through
here, so they cannot drift apart. A user-level engine is not called without
a user timeline, a post-level engine is not called without a post, and the
result says which were left out. The caller may also name the engines to
run; nothing here routes or cascades on its behalf.

The four API-backed engines are I/O bound and go out together on a small
thread pool. The encoders stay in order, since they share one process-wide
model. Each adapter builds its own client inside `predict` and the catalogue
list is only read during a request, so nothing is shared across the pool.
Set GEOLENS_MAX_CONCURRENCY=1 to put them back in order.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from concurrent.futures import ThreadPoolExecutor

from geolens.engines.base import Engine, GeolocateInput, Prediction, skipped_prediction

NO_TIMELINE = "no user timeline supplied"
NO_POST = "no post supplied"
NOT_SELECTED = "not selected"

# The adapters worth overlapping: both are a single HTTPS round trip.
CONCURRENT_ENGINES = frozenset({"llm_gpt4o_mini", "llm_claude_haiku"})

DEFAULT_MAX_CONCURRENCY = 4


def has_user_timeline(payload: GeolocateInput) -> bool:
    """True when the input carries a user timeline the user-level engines can read."""
    return bool(payload.user_posts)


def has_post(payload: GeolocateInput) -> bool:
    """True when the input carries a post the post-level engines can read."""
    return bool(payload.post)


def max_concurrency() -> int:
    """Pool size for the API-backed engines. 1 dispatches them in order."""
    try:
        return max(1, int(os.getenv("GEOLENS_MAX_CONCURRENCY", str(DEFAULT_MAX_CONCURRENCY))))
    except ValueError:
        return DEFAULT_MAX_CONCURRENCY


def run_engines(
    engines: Mapping[str, Engine],
    payload: GeolocateInput,
    *,
    k: int = 5,
    selected: Collection[str] | None = None,
) -> dict[str, Prediction]:
    """Run every engine that has something to read, keyed by engine name.

    The returned mapping always has one entry per engine, in the order the
    caller listed them, so a caller can show the whole roster; an engine that
    was not run carries a skipped result. `selected` is the set of engine
    names the caller asked for; None means all of them.
    """
    timeline = has_user_timeline(payload)
    post = has_post(payload)
    results: dict[str, Prediction] = {}
    pooled: list[tuple[str, Engine]] = []

    for name, engine in engines.items():
        if selected is not None and name not in selected:
            results[name] = skipped_prediction(name, reason=NOT_SELECTED)
        elif engine.granularity == "user" and not timeline:
            results[name] = skipped_prediction(name, reason=NO_TIMELINE)
        elif engine.granularity == "post" and not post:
            results[name] = skipped_prediction(name, reason=NO_POST)
        elif getattr(engine, "name", "") in CONCURRENT_ENGINES:
            pooled.append((name, engine))
        else:
            results[name] = engine.predict(payload, k=k)

    workers = min(max_concurrency(), len(pooled))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geolens") as pool:
            futures = {name: pool.submit(e.predict, payload, k) for name, e in pooled}
            for name, future in futures.items():
                results[name] = future.result()
    else:
        for name, engine in pooled:
            results[name] = engine.predict(payload, k=k)

    return {name: results[name] for name in engines}
