"""Run manifest for reproducible evaluation.

``build_manifest`` records which model versions ran, over what candidate
catalogue, with what ``k`` and fusion method, against what input, and when.

``catalogue`` is the list of places with each point and the distance method
rather than a hash alone, because an onboarded place expires after an hour and
a hash cannot be resolved back afterwards, and because a distance metric has to
be recomputable from the manifest. ``input_sha256`` and ``input_row_count``
identify what was sent. ``sampling_parameters`` is read off the adapters rather
than restated. ``git_commit`` names the build, and ``price_table`` records the
listed prices ``cost_usd`` was estimated from and the date they were read.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from geolens import API_VERSION, __version__
from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._coords import coords_for, feature_for
from geolens.geo import EARTH_RADIUS_KM
from geolens.onboarding.catalogue import is_default_city
from geolens.onboarding.wizard import cached_profile
from geolens.places import place_id
from geolens.pricing import price_table

DISTANCE_METHOD = (
    f"haversine on a sphere of radius {EARTH_RADIUS_KM} km; "
    "no ellipsoidal correction is applied"
)


# Where a build that carries no git metadata records its commit. The image
# build writes the file into the package; `GEOLENS_GIT_COMMIT` overrides it.
BUILD_COMMIT_FILE = Path(__file__).parent / "data" / "build_commit.txt"
BUILD_COMMIT_ENV = "GEOLENS_GIT_COMMIT"


def _git(*args: str) -> str:
    """Run one git command in the checkout this package lives in, or return ""."""
    repo = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _build_commit() -> str | None:
    """The commit a build without git metadata was made from, or None.

    A container is built from a source copy with no `.git`, so the image
    build bakes the commit in: `GEOLENS_GIT_COMMIT` in the environment, or
    the file the build writes into the package.
    """
    from_env = (os.getenv(BUILD_COMMIT_ENV) or "").strip()
    if from_env:
        return from_env
    try:
        baked = BUILD_COMMIT_FILE.read_text().strip()
    except OSError:
        return None
    return baked or None


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    """The commit this build was made from, or None when nothing records it.

    Read once: the commit cannot change while the process runs. A checkout
    answers from git; an image answers from the commit baked in at build
    time.
    """
    return _git("rev-parse", "HEAD") or _build_commit()


@lru_cache(maxsize=1)
def git_dirty() -> bool | None:
    """Whether the checkout had uncommitted changes when the process started.

    None for a build with no checkout, where there is nothing to compare.
    """
    if not _git("rev-parse", "HEAD"):
        return None
    return bool(_git("status", "--porcelain"))


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


def input_fingerprint(rows: list[dict[str, Any]]) -> str:
    """SHA-256 of the input rows, so a result can be tied to what was sent.

    The rows are serialised canonically, so the same rows uploaded as CSV and
    posted as JSON produce the same digest.
    """
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def catalogue_detail(
    catalogue: list[str], ages_minutes: dict[str, float] | None = None
) -> dict[str, Any]:
    """The catalogue as a list rather than a hash.

    Every place carries its identifier and its coordinate, so a distance
    metric reported against this run can be recomputed from the manifest
    alone; `built_in_names` keeps the plain list of names a reader of the
    older shape expects. An onboarded place also carries its aliases and how
    long it has been in the catalogue, neither of which survives its expiry.
    """
    ages = ages_minutes or {}
    built_in: list[dict[str, Any]] = []
    built_in_names: list[str] = []
    onboarded: list[dict[str, Any]] = []
    for name in catalogue:
        coords = coords_for(name)
        if is_default_city(name):
            feature_type, centroid_source = feature_for(name)
            built_in_names.append(name)
            built_in.append(
                {
                    "name": name,
                    "place_id": place_id(name),
                    "lat": None if coords is None else coords[0],
                    "lon": None if coords is None else coords[1],
                    "feature_type": feature_type,
                    "centroid_source": centroid_source,
                }
            )
            continue
        profile = cached_profile(name)
        onboarded.append(
            {
                "name": name,
                "place_id": place_id(name),
                "lat": None if profile is None else profile.lat,
                "lon": None if profile is None else profile.lon,
                "aliases": [] if profile is None else list(profile.aliases),
                "age_minutes": ages.get(name),
            }
        )
    return {
        "built_in": built_in,
        "built_in_names": built_in_names,
        "built_in_sha": catalogue_hash(list(DEFAULT_CITIES)),
        "onboarded": onboarded,
        "distance_method": DISTANCE_METHOD,
    }


def sampling_parameters(engines: dict[str, Any]) -> dict[str, Any]:
    """What each engine sends to its model, read off the adapters.

    An engine that calls no model is left out rather than given an empty
    entry, and nothing here is restated by hand: the Claude adapter sends no
    temperature at all, and a manifest that claimed one would be wrong.
    """
    out: dict[str, Any] = {}
    for name, engine in engines.items():
        params = getattr(engine, "sampling_parameters", None)
        if params:
            out[name] = dict(params)
    return out


CALL_MODES = ("real", "stub", "failed", "skipped")


def call_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per-engine counts of real, stub, failed and skipped calls across a batch.

    Each row is a ``{engine_name: Prediction}`` mapping, so the counts say what
    the run actually did rather than what it was configured to do.
    """
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        for name, pred in row.items():
            per_engine = counts.setdefault(name, dict.fromkeys(CALL_MODES, 0))
            per_engine[pred.mode] = per_engine.get(pred.mode, 0) + 1
    return counts


def _observed_mode(counts: dict[str, int]) -> str:
    """One label for an engine's calls: the single mode, or ``mixed``."""
    seen = [mode for mode, n in counts.items() if n]
    if len(seen) == 1:
        return seen[0]
    return "mixed" if seen else "none"


def build_manifest(
    engines: dict[str, Any],
    catalogue: list[str],
    *,
    k: int,
    ensemble_method: str,
    flag_radius_km: float | None = None,
    predictions: dict[str, Any] | None = None,
    counts: dict[str, dict[str, int]] | None = None,
    selected_engines: list[str] | None = None,
    input_sha256: str | None = None,
    input_row_count: int | None = None,
    base_url: str | None = None,
    catalogue_ages_minutes: dict[str, float] | None = None,
    gazetteer_abstentions: dict[str, int] | None = None,
    gazetteer_matches: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run metadata for one response.

    ``predictions`` is a single response's ``{engine: Prediction}`` mapping and
    ``counts`` a batch's per-engine call counts. When either is given,
    ``engine_modes`` reports what each call did rather than how the engines
    were configured, so a live call that raised is not recorded as ``real``.
    """
    if predictions is not None:
        engine_modes = {name: pred.mode for name, pred in predictions.items()}
    elif counts is not None:
        engine_modes = {name: _observed_mode(c) for name, c in counts.items()}
    else:
        engine_modes = {
            name: ("stub" if getattr(e, "stub", False) else "real")
            for name, e in engines.items()
        }

    manifest: dict[str, Any] = {
        "tool": "GeoLens",
        "version": __version__,
        "api_version": API_VERSION,
        # The checkout this build was made from. Null when the package was
        # installed from a wheel or copied without its git metadata.
        "git_commit": git_commit(),
        "git_dirty": git_dirty(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": k,
        "ensemble_method": ensemble_method,
        "distance_method": DISTANCE_METHOD,
        # What cost_usd was estimated from. No cost here is read from a bill.
        "price_table": price_table(),
        "catalogue_size": len(catalogue),
        "catalogue_sha": catalogue_hash(catalogue),
        "engines": {name: _engine_model(e) for name, e in engines.items()},
        # Whether each engine returned live model output, the keyless
        # placeholder, or an error, so a number is never silently attributed to
        # a real model that did not actually run.
        "engine_modes": engine_modes,
        # Which engines the caller asked for. All of them unless the caller
        # named a subset; the rest come back marked not selected.
        "selected_engines": (
            list(engines) if selected_engines is None else list(selected_engines)
        ),
        "sampling_parameters": sampling_parameters(engines),
        "catalogue": catalogue_detail(catalogue, catalogue_ages_minutes),
    }
    if flag_radius_km is not None:
        manifest["flag_radius_km"] = flag_radius_km
    if counts is not None:
        manifest["engine_call_counts"] = counts
    if input_sha256 is not None:
        manifest["input_sha256"] = input_sha256
    if input_row_count is not None:
        manifest["input_row_count"] = input_row_count
    if base_url is not None:
        manifest["base_url"] = base_url
    if gazetteer_abstentions is not None:
        # How many rows named no catalogue place at each level, so a
        # distribution table's counts can be read against them.
        manifest["n_gazetteer_abstained"] = gazetteer_abstentions
    if gazetteer_matches is not None:
        manifest["n_gazetteer_matched"] = gazetteer_matches
    return manifest
