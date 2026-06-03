"""Run manifest for reproducible evaluation.

A reported number is only reproducible if you know what produced it: which
model versions ran, over what candidate catalogue, with what k and fusion
method, and when. ``build_manifest`` captures that so an eval result can be
cited unambiguously.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from geolens import __version__


def _engine_model(engine: Any) -> str:
    """Best-effort model identifier for an engine: LLM model, encoder, or rule."""
    for attr in ("model", "encoder"):
        val = getattr(engine, attr, None)
        if val:
            return str(val)
    return "rule-based"


def catalogue_hash(catalogue: list[str]) -> str:
    """Stable short hash of the candidate catalogue (order-independent)."""
    joined = "\n".join(sorted(c.strip().lower() for c in catalogue))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def build_manifest(
    engines: dict[str, Any],
    catalogue: list[str],
    *,
    k: int,
    ensemble_method: str,
) -> dict[str, Any]:
    return {
        "tool": "GeoLens",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": k,
        "ensemble_method": ensemble_method,
        "catalogue_size": len(catalogue),
        "catalogue_sha": catalogue_hash(catalogue),
        "engines": {name: _engine_model(e) for name, e in engines.items()},
        # Whether each engine is configured for live inference or the offline
        # stub fallback, so a number is never silently attributed to a real
        # model that did not actually run.
        "engine_modes": {
            name: ("stub" if getattr(e, "stub", False) else "real")
            for name, e in engines.items()
        },
    }
