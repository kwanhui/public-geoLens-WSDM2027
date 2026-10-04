"""FastAPI routes for the GeoLens workbench.

The endpoints are `/healthz`, `/geolocate`, `/catalogue`, `/onboard` (POST, PUT,
DELETE and `/onboard/status`), `/instance`, `/batch_predict`, `/eval`, the two
`_csv` forms and the static page. Every bound the server enforces is declared on
the request models, so it appears in the OpenAPI document at `/docs`, and every
error leaves through the one envelope in `geolens.ui.errors`.

The catalogue is one shared mutable list: a request takes the read side of
`CatalogueLock` for its whole engine run and its manifest, and an onboarding
takes the write side. Per-address budgets and the estimated-spend ceiling are
off unless a deployment sets them; `demo/deployment.md` lists every setting with
its package default and its hosted value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from geolens import API_VERSION, __version__
from geolens.batch import BatchInput, compute_summary, run_batch
from geolens.batch.metrics import (
    bucket_of,
    compute_rollup,
    gazetteer_abstentions,
    gazetteer_matches,
    truth_for,
)
from geolens.dispatch import run_engines
from geolens.engine_reference import engine_reference
from geolens.engines._coords import coords_for, feature_for
from geolens.engines.base import GeolocateInput, Prediction, skipped_prediction
from geolens.engines.registry import build_engines, engine_metadata
from geolens.ensemble import as_fusion_method, ensemble
from geolens.flag_reference import flag_reference
from geolens.geo import ACC_KM_THRESHOLD, EARTH_RADIUS_KM, haversine_km
from geolens.manifest import (
    build_manifest,
    call_counts,
    catalogue_hash,
    git_commit,
    input_fingerprint,
)
from geolens.onboarding import (
    CityProfile,
    OnboardingRegistry,
    ProfileError,
    cached_profile,
    find_in_catalogue,
    is_default_city,
    onboard_city,
    profile_warnings,
    proximity_warning,
    save_profile,
    validate_coordinate,
    validate_list,
    validate_notes,
    validate_place_name,
    validate_region,
)
from geolens.onboarding.lock import CatalogueLock
from geolens.onboarding.validation import (
    MAX_ALIASES,
    MAX_ITEM_LENGTH,
    MAX_LANDMARKS,
    MAX_LIST_ITEMS,
    MAX_NAME_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_PLACE_NAME_CHARS,
    MAX_PLACE_NAME_WORDS,
    MIN_ALIAS_LENGTH,
    address_like_reason,
    confusable_catalogue_place,
    swallowed_catalogue_place,
)
from geolens.places import name_key, normalise_text, place_id
from geolens.spend import SpendLedger
from geolens.triangulator import triangulate
from geolens.ui.errors import ApiError, install_error_handlers
from geolens.ui.limits import (
    MAX_BATCH_CHARS,
    MAX_BATCH_INPUTS,
    MAX_HANDLE_CHARS,
    MAX_K,
    MAX_POST_CHARS,
    MAX_ROW_ID_CHARS,
    MAX_TIMELINE_CHARS,
    MAX_TIMELINE_POSTS,
    MIN_K,
    FusionMethodName,
    InputError,
    check_fusion_method,
    check_k,
    clean_handle,
    clean_post,
    clean_timeline,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ----- the vocabularies the responses use ------------------------------------
# Declared here rather than left as free strings, so they appear as enums in
# the OpenAPI document and a client does not have to discover them by
# running the server.

SPEND_CEILING_SKIP = "the instance's estimated spend ceiling is reached"

RowStatusName = Literal["ok", "error", "ooc"]
EngineModeName = Literal["real", "stub", "failed", "skipped"]
# What a batch manifest reports per engine: the single mode when an engine's
# calls all did the same thing, `mixed` when they did not, `none` when the
# engine made no call at all.
ObservedModeName = Literal["real", "stub", "failed", "skipped", "mixed", "none"]
SkipReasonName = Literal[
    "",
    "no user timeline supplied",
    "no post supplied",
    "not selected",
    "the instance's estimated spend ceiling is reached",
]
# Why a call that returned carries no place. Empty unless the reply named
# nothing in the closed catalogue, which is a content outcome, not an error.
OutcomeName = Literal["", "no_catalogue_place"]
GranularityName = Literal["post", "user"]
CatalogueSourceName = Literal["built-in", "onboarded"]
ProfileSourceName = Literal["openai", "stub", "edited"]
FeatureTypeName = Literal[
    "country", "city", "town", "estate", "street", "onboarded-unverified"
]
RowErrorPolicy = Literal["fail", "skip"]

DISTANCE_METHOD = (
    "haversine on a sphere of radius "
    f"{EARTH_RADIUS_KM} km; no ellipsoidal correction is applied"
)

# The ground-truth columns /eval can score against.
TRUTH_HEADERS = frozenset({"ground_truth_city", "ground_truth_user_city"})

# The CSV forms take the same settings as the JSON bodies, and say the same
# thing about each, so the OpenAPI document describes both the same way.
K_DESCRIPTION = "How many places each engine returns, ranked. The fusion reads the whole list."
ENSEMBLE_DESCRIPTION = (
    "How the engines of one level are fused: `weighted` sums each engine's "
    "top-k scores, `rrf` applies reciprocal rank fusion. It does not affect "
    "the verification flag."
)
FLAG_RADIUS_DESCRIPTION = (
    "Great-circle separation, in km, at which the verification flag is raised "
    f"when the two consensus places differ. Defaults to {ACC_KM_THRESHOLD:.0f} km."
)
ENGINES_DESCRIPTION = (
    "Comma-separated engine names from the registry GET /instance returns. "
    "Omit it to run every engine; one left out is reported as not selected."
)
ON_ROW_ERROR_DESCRIPTION = (
    '`fail`, the default, refuses the whole upload with a 422 naming the row. '
    '`skip` processes the rest and returns the bad row with `status: "error"`.'
)

# Every limit is read on each call rather than captured at import, so a
# process that sets the variable after importing the module gets the limit it
# asked for. `geolens.paths` does the same for the cache root.

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def max_queries_per_hour() -> int:
    """Inference calls per address per hour. 0, the default, is no limit.

    A local run is one person on their own machine paying their own bill, so
    the limit belongs to the deployment rather than to the package; the
    Dockerfile sets it for the hosted instance.
    """
    return _int_env("MAX_QUERIES_PER_HOUR", 0)


def max_profile_saves_per_hour() -> int:
    """Profile saves and deletions per address per hour. 0 is no limit."""
    return _int_env("MAX_PROFILE_SAVES_PER_HOUR", 0)


def max_batch_rows() -> int:
    return _int_env("MAX_BATCH_ROWS", 50)


def max_batch_bytes() -> int:
    return _int_env("MAX_BATCH_BYTES", 200_000)  # ~200 KB CSV


def max_batches_per_hour() -> int:
    """Bulk runs per address per hour. 0, the default, is no limit."""
    return _int_env("MAX_BATCHES_PER_HOUR", 0)


def trusted_proxy_hops() -> int:
    """How many proxies sit in front of this instance.

    Each one appends the address it received the request from to
    X-Forwarded-For, so the address the trusted proxy added is the Nth entry
    from the right. With 0 the header is ignored entirely, which is right for
    a direct local run: a client could otherwise send any value and reset its
    own rate limit. Hugging Face Spaces sets 1.
    """
    return _int_env("GEOLENS_TRUSTED_PROXY_HOPS", 0)


def require_region() -> bool:
    """Whether onboarding refuses a place name with no country or region hint.

    On a shared instance a bare name is ambiguous enough to be worth refusing
    outright; the Space sets this.
    """
    return os.getenv("GEOLENS_REQUIRE_REGION", "0") == "1"


def require_edit_token() -> bool:
    """Whether editing or removing a place needs the token its drafting returned.

    Off by default, because a local run is one operator on their own machine.
    The Space sets it: there the catalogue is shared and anyone could
    otherwise rewrite or delete anyone else's place.
    """
    return os.getenv("GEOLENS_REQUIRE_EDIT_TOKEN", "0") == "1"


def operator_token() -> str:
    """The token that may edit or remove any place. Empty means none is set."""
    return (os.getenv("GEOLENS_OPERATOR_TOKEN") or "").strip()


def refuse_address_like() -> bool:
    """Whether onboarding refuses a name that reads as a building or an address.

    A place is a neighbourhood or a town. Off by default it is a warning; the
    Space refuses, because a shared instance should not be asked to geolocate
    against one person's home.
    """
    return os.getenv("GEOLENS_REFUSE_ADDRESS_LIKE", "0") == "1"


def log_salt() -> str:
    """The salt an address is hashed with before it reaches the log.

    A deployment that wants one log line to be comparable with the next sets
    `GEOLENS_LOG_SALT`; otherwise the salt is new on every start, so the
    hashes are useless outside one process lifetime, which is the point.
    """
    return os.getenv("GEOLENS_LOG_SALT") or _PROCESS_SALT


def reconcile_seconds() -> float:
    """How often the cache directory is reconciled with the registry."""
    try:
        return max(0.0, float(os.getenv("GEOLENS_RECONCILE_SECONDS", "300")))
    except ValueError:
        return 300.0


_PROCESS_SALT = secrets.token_hex(16)

# The JSON endpoints whose body the byte cap applies to.
JSON_BATCH_PATHS = frozenset({"/batch_predict", "/eval"})

# One warning per process when the forwarded header a deployment says to
# trust never arrives: the rate limiter would be counting the proxy instead.
_forwarded_header_warned = False


def _warn_about_missing_forwarded_header(request: Request) -> None:
    """Say once that the trusted proxy hop is configured but not arriving."""
    global _forwarded_header_warned
    if _forwarded_header_warned or trusted_proxy_hops() <= 0:
        return
    if request.headers.get("x-forwarded-for"):
        return
    _forwarded_header_warned = True
    logger.warning(
        "GEOLENS_TRUSTED_PROXY_HOPS is %d but this request carried no "
        "X-Forwarded-For header, so every caller counts against one address. "
        "Set it to 0 for a direct run.",
        trusted_proxy_hops(),
    )


def hashed_address(address: str) -> str:
    """A salted digest of a client address, short enough to read in a log."""
    digest = hashlib.sha256(f"{log_salt()}:{address}".encode()).hexdigest()
    return digest[:16]

# Row ids that read as an account handle rather than a record key: a leading
# "@", or the underscore-joined form handles are usually written in.
HANDLE_PATTERN = re.compile(
    r"^@[A-Za-z0-9_]{1,30}$|^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]{1,28}$"
)

# Bounds on the verification flag's radius. The lower bound keeps a request
# from flagging every pair of distinct cities; the upper bound is half the
# earth's great circle, past which no two points can be further apart.
MIN_FLAG_RADIUS_KM = 1.0
MAX_FLAG_RADIUS_KM = 20015.0

FLAG_RADIUS_FIELD: Any = Field(
    default=ACC_KM_THRESHOLD,
    ge=MIN_FLAG_RADIUS_KM,
    le=MAX_FLAG_RADIUS_KM,
    allow_inf_nan=False,
    description=(
        "Great-circle separation, in km, at which the verification flag is "
        "raised when the post-level and user-level consensus places differ. "
        f"Defaults to {ACC_KM_THRESHOLD:.0f} km (100 miles), the value every "
        f"reported number was produced under. Distances are measured by "
        f"{DISTANCE_METHOD}."
    ),
)


# ----- Request / response models ---------------------------------------------

def _bounded(clean, value):
    """Apply one of the `limits` cleaners, turning a breach into a 422."""
    try:
        return clean(value)
    except InputError as e:
        raise ValueError(str(e)) from e


class StrictModel(BaseModel):
    """A request body that refuses a field it does not know.

    A misspelt parameter is a 422 naming it, rather than a 200 computed at
    the default with nothing to say the setting was dropped.
    """

    model_config = ConfigDict(extra="forbid")


ENGINES_FIELD: Any = Field(
    default=None,
    max_length=64,
    description=(
        "Engine names to run, from the registry returned by GET /instance. "
        "Omit it to run all of them. An engine left out is reported as not "
        "selected. The caller makes this choice, and nothing routes or "
        "cascades on the caller's behalf."
    ),
)

K_FIELD: Any = Field(
    default=5,
    ge=MIN_K,
    le=MAX_K,
    description="How many places each engine returns, ranked. The fusion reads the whole list.",
)

ENSEMBLE_METHOD_FIELD: Any = Field(
    default="weighted",
    description=(
        "How the engines of one level are fused: `weighted` sums each engine's "
        "top-k scores, `rrf` applies reciprocal rank fusion over the ranks "
        "alone. It does not affect the verification flag, which compares each "
        "level's consensus place."
    ),
)

ON_ROW_ERROR_FIELD: Any = Field(
    default="fail",
    description=(
        "What one unusable row does to the run. `fail`, the default, refuses "
        "the whole request with a 422 naming the row. `skip` processes the "
        "rest and returns the bad row with `status: \"error\"` and a per-row "
        "`error`, counted in `summary.error_rows`."
    ),
)

POST_FIELD: Any = Field(
    default=None,
    max_length=MAX_POST_CHARS,
    description=f"A single post, at most {MAX_POST_CHARS:,} characters.",
)

USER_POSTS_FIELD: Any = Field(
    default=None,
    max_length=MAX_TIMELINE_POSTS,
    description=(
        f"Recent posts from one account, for the user-level engines. At most "
        f"{MAX_TIMELINE_POSTS} posts of {MAX_POST_CHARS:,} characters each, and "
        "20,000 characters in total."
    ),
)

USER_HANDLE_FIELD: Any = Field(
    default=None,
    max_length=MAX_HANDLE_CHARS,
    description=(
        "Ignored: no engine reads it, it does not satisfy the requirement for "
        "a post or a timeline, and it is not echoed on a batch row."
    ),
)


class GeolocateRequest(StrictModel):
    post: str | None = POST_FIELD
    engines: list[str] | None = ENGINES_FIELD
    user_handle: str | None = USER_HANDLE_FIELD
    user_posts: list[str] | None = USER_POSTS_FIELD
    k: int = K_FIELD
    ensemble_method: FusionMethodName = ENSEMBLE_METHOD_FIELD
    flag_radius_km: float = FLAG_RADIUS_FIELD

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "post": "Fire at Marina Bay Sands",
                    "user_posts": ["Ramen in Shibuya again", "Tokyo is cold tonight"],
                    "k": 5,
                    "ensemble_method": "weighted",
                    "flag_radius_km": 161.0,
                }
            ]
        },
    )

    _clean_post = field_validator("post")(lambda cls, v: _bounded(clean_post, v))
    _clean_posts = field_validator("user_posts")(lambda cls, v: _bounded(clean_timeline, v))
    _clean_handle = field_validator("user_handle")(lambda cls, v: _bounded(clean_handle, v))


class EnginePrediction(BaseModel):
    city: str = Field(description="The place this engine named, or an empty string.")
    place_id: str | None = Field(
        default=None,
        description=(
            "Opaque stable identifier of `city`, or null when the engine named "
            "no place. Derived from the place's normalised name, so it is the "
            "same on every instance."
        ),
    )
    place_coordinates: list[float] | None = Field(
        default=None, description="[lat, lon] of `city`, or null when it has no coordinate."
    )
    confidence: float
    top_k: list[tuple[str, float]]
    latency_ms: float
    cost_usd: float = Field(
        description=(
            "Estimated from the listed per-token prices recorded in the run "
            "manifest, not read from a bill. Zero for an engine that calls no "
            "model and for a model whose price is not listed."
        )
    )
    cost_is_estimated: bool = Field(
        default=False,
        description="True when `cost_usd` was estimated from a listed price.",
    )
    note: str
    mode: EngineModeName = "real"
    abstain: bool = False
    evidence: str = ""
    # A live call that raised, could not be read, or named nothing in the
    # catalogue. It carries no city and is excluded from fusion, from the
    # consensus and from the flag. Read `outcome` to tell the last of those
    # from the first two.
    failed: bool = False
    error_class: str = ""
    outcome: OutcomeName = Field(
        default="",
        description=(
            "Why a call that returned carries no place. "
            '`no_catalogue_place` means the model answered and named nothing '
            "in the closed catalogue, which is a content outcome rather than "
            "an error. Empty otherwise."
        ),
    )
    # An engine that was never called, because the input carries nothing it
    # can read (a user-level engine with no timeline).
    skipped: bool = False
    reason: SkipReasonName = ""
    # Set on a /eval row where the level this engine answers at has a truth.
    error_km: float | None = Field(
        default=None,
        description=(
            "Great-circle distance from this engine's place to the row's truth "
            "for this engine's level, to one decimal. Null when either has no "
            f"coordinate, when the engine abstained, or on a run with no truth. "
            f"Measured by {DISTANCE_METHOD}."
        ),
    )
    within_161km: bool | None = Field(
        default=None,
        description=(
            "Whether `error_km` is within the 161 km (100 mile) convention. "
            "Null wherever `error_km` is null."
        ),
    )


def _engine_view(pred: Prediction) -> EnginePrediction:
    coords = coords_for(pred.city)
    return EnginePrediction(
        city=pred.city,
        place_id=place_id(pred.city) if pred.city else None,
        place_coordinates=None if coords is None else [coords[0], coords[1]],
        confidence=pred.confidence,
        top_k=pred.top_k,
        latency_ms=pred.latency_ms,
        cost_usd=pred.cost_usd,
        cost_is_estimated=pred.cost_is_estimated,
        note=pred.note,
        mode=pred.mode,  # type: ignore[arg-type]
        abstain=pred.abstain,
        evidence=pred.evidence,
        failed=pred.failed,
        error_class=pred.error_class,
        outcome=pred.outcome,  # type: ignore[arg-type]
        skipped=pred.skipped,
        reason=pred.reason,  # type: ignore[arg-type]
    )


class TriangulationView(BaseModel):
    consensus_city: str
    consensus_confidence: float
    agreement_score: float
    disagreement_flag: bool
    post_consensus_city: str = ""
    user_consensus_city: str = ""
    # Null rather than an empty string when a level reached no consensus.
    post_consensus_place_id: str | None = None
    user_consensus_place_id: str | None = None
    disagreement_km: float | None = None
    disagreement_score: float = 0.0
    # How thin the majority behind each consensus place was.
    post_consensus_votes: int = 0
    post_consensus_answered: int = 0
    user_consensus_votes: int = 0
    user_consensus_answered: int = 0
    notes: list[str]


class EnsembleView(BaseModel):
    granularity: GranularityName
    consensus_city: str
    consensus_place_id: str | None = None
    consensus_confidence: float
    method: FusionMethodName = "weighted"
    top_k: list[tuple[str, float]]
    contributing_engines: list[str]
    best_single_engine: str
    best_single_city: str
    best_single_confidence: float
    delta_vs_best_single: float
    differs_from_best_single: bool


class PlaceRef(BaseModel):
    """One place named anywhere in a response, with everything needed to map it."""

    place_id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    source: CatalogueSourceName = "built-in"
    feature_type: FeatureTypeName | None = None
    centroid_source: str | None = None


class RunManifestView(BaseModel):
    """Run metadata carried by every response that runs an engine.

    Declared so a reader of the OpenAPI document knows what is in it. Extra
    keys are kept: the manifest gains fields as the workbench does, and a
    client that reads one by name should keep seeing it.
    """

    model_config = ConfigDict(extra="allow")

    tool: str = "GeoLens"
    version: str | None = None
    api_version: str | None = None
    git_commit: str | None = Field(
        default=None,
        description="The tool's git commit, when the build was made from a checkout.",
    )
    git_dirty: bool | None = None
    generated_at: str | None = None
    k: int | None = None
    ensemble_method: FusionMethodName | None = None
    flag_radius_km: float | None = None
    distance_method: str | None = None
    catalogue_size: int | None = None
    catalogue_sha: str | None = None
    catalogue: dict[str, Any] | None = None
    engines: dict[str, str] | None = None
    engine_modes: dict[str, ObservedModeName] | None = None
    engine_call_counts: dict[str, dict[str, int]] | None = None
    selected_engines: list[str] | None = None
    sampling_parameters: dict[str, Any] | None = None
    price_table: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The per-token prices `cost_usd` was estimated from, and the date "
            "they were recorded. Costs are estimates from listed prices, never "
            "read from a bill."
        ),
    )
    input_sha256: str | None = None
    input_row_count: int | None = None
    base_url: str | None = None
    n_gazetteer_abstained: dict[str, int] | None = None
    n_gazetteer_matched: dict[str, int] | None = None


class SpendView(BaseModel):
    """The instance's estimated-spend ceiling and what has been spent against it.

    Every figure is an estimate from the listed prices in the run manifest,
    not a number read from a bill. A ceiling of 0 is off. While
    `ceiling_reached` is true the engines that call a paid model are not
    called and come back skipped with `reason`; the local engines answer as
    usual.
    """

    estimated_usd_last_hour: float
    estimated_usd_last_day: float
    max_usd_per_hour: float = Field(description="0 means no hourly ceiling.")
    max_usd_per_day: float = Field(description="0 means no daily ceiling.")
    ceiling_reached: bool
    reason: str = Field(
        default="", description="Why the ceiling bites, or empty when it does not."
    )


class GeolocateResponse(BaseModel):
    per_engine: dict[str, EnginePrediction]
    triangulation: TriangulationView
    ensembles: dict[str, EnsembleView] = {}
    # Coordinate of every place named in this response, read from the shared
    # catalogue, so an onboarded place pins for every visitor rather than
    # only in the browser tab that onboarded it.
    place_coordinates: dict[str, list[float]] = Field(
        default={}, description="Place name to [lat, lon], for every place named here."
    )
    places: list[PlaceRef] = Field(
        default=[],
        description=(
            "The same places as `place_coordinates`, each with its `place_id`, "
            "its feature type and where its stored point came from."
        ),
    )
    spend: SpendView | None = None
    manifest: RunManifestView | None = None


class OnboardRequest(StrictModel):
    city: str = Field(max_length=MAX_NAME_LENGTH, description="The place to draft a profile for.")
    # Optional country or region hint, free text ("Singapore", "West
    # Kalimantan, Indonesia"). Goes into the drafting prompt and is checked
    # against the drafted centroid.
    region: str = Field(
        default="",
        max_length=MAX_ITEM_LENGTH,
        description=(
            "Country or region hint, free text. It goes into the drafting "
            "prompt, is stored on the profile and is checked against the "
            "drafted coordinate. Required when the instance sets "
            "GEOLENS_REQUIRE_REGION, which GET /instance reports."
        ),
    )
    force_refresh: bool = Field(
        default=False, description="Redraft even when a profile is cached for this place."
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"city": "Bidadari Estate", "region": "Singapore"}]
        },
    )


EDIT_TOKEN_FIELD: Any = Field(
    default="",
    max_length=200,
    description=(
        "The token `POST /onboard` returned for this place. Required when the "
        "instance sets GEOLENS_REQUIRE_EDIT_TOKEN, which `GET /instance` "
        "reports as `require_edit_token`. It may also be sent as the "
        "`X-GeoLens-Edit-Token` header."
    ),
)


class ResetRequest(StrictModel):
    city: str = Field(max_length=MAX_NAME_LENGTH)
    edit_token: str = EDIT_TOKEN_FIELD

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"city": "Bidadari Estate"}]}
    )


class SaveProfileRequest(StrictModel):
    name: str = Field(
        max_length=MAX_NAME_LENGTH,
        description=(
            "The place whose profile this is. It must already be in the "
            f"catalogue: PUT edits, it does not create. At most "
            f"{MAX_PLACE_NAME_CHARS} characters and {MAX_PLACE_NAME_WORDS} "
            "words, letters, marks, digits, spaces, hyphens, apostrophes and "
            "full stops only."
        ),
    )
    edit_token: str = EDIT_TOKEN_FIELD
    aliases: list[str] = Field(
        default=[],
        max_length=MAX_ALIASES,
        description=(
            f"Other names the place is known by. At most {MAX_ALIASES} entries "
            f"of {MAX_ITEM_LENGTH} characters and {MIN_ALIAS_LENGTH} characters "
            "or more each. An alias has to read as a name: at most four words, "
            "not made only of common words, and with a word that is either "
            "capitalised or shares a stem with the place name."
        ),
    )
    landmarks: list[str] = Field(
        default=[],
        max_length=MAX_LANDMARKS,
        description=(
            f"Well-known places inside it. At most {MAX_LANDMARKS} entries, "
            "under the same rule as an alias: the gazetteer counts both "
            "against every post."
        ),
    )
    foods: list[str] = Field(default=[], max_length=MAX_LIST_ITEMS)
    slang: list[str] = Field(default=[], max_length=MAX_LIST_ITEMS)
    notes: str = Field(default="", max_length=MAX_NOTES_LENGTH)
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    region: str = Field(
        default="",
        max_length=MAX_ITEM_LENGTH,
        description=(
            "Country or region hint. Left empty, the hint stored with the "
            "profile is kept and the coordinate is checked against that."
        ),
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Bidadari Estate",
                    "aliases": ["Bidadari", "Bidadari BTO"],
                    "landmarks": ["Bidadari Park", "Woodleigh MRT"],
                    "foods": ["kaya toast"],
                    "slang": [],
                    "notes": "Housing estate in north-east Singapore.",
                    "lat": 1.3395,
                    "lon": 103.8722,
                    "region": "Singapore",
                }
            ]
        },
    )


# ----- Batch I/O models ------------------------------------------------------

class BatchInputRow(StrictModel):
    id: str = Field(
        min_length=1,
        max_length=MAX_ROW_ID_CHARS,
        description=(
            "A stable identifier for the row, unique within the request. It is "
            "returned on the matching result row and is what an export joins on."
        ),
    )
    post: str | None = POST_FIELD
    user_handle: str | None = USER_HANDLE_FIELD
    user_posts: list[str] | None = USER_POSTS_FIELD
    ground_truth_city: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="The place the post was written in. Scored against the post-level engines.",
    )
    ground_truth_user_city: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description=(
            "The home place of the account behind `user_posts`. When present "
            "the user-level engines are scored against it and the post-level "
            "ones against `ground_truth_city`."
        ),
    )
    bucket: str | None = Field(
        default=None,
        max_length=MAX_ITEM_LENGTH,
        description="Difficulty label for the per-bucket breakdown; derived from the id prefix when absent.",
    )
    should_disagree: bool | None = Field(
        default=None,
        description=(
            "Gold label for the verification flag. Not echoed on the result "
            "row; it reaches the run only through `summary.banner`."
        ),
    )

    _clean_post = field_validator("post")(lambda cls, v: _bounded(clean_post, v))
    _clean_posts = field_validator("user_posts")(lambda cls, v: _bounded(clean_timeline, v))
    _clean_handle = field_validator("user_handle")(lambda cls, v: _bounded(clean_handle, v))


class BatchRequest(StrictModel):
    inputs: list[BatchInputRow] = Field(
        min_length=1,
        max_length=MAX_BATCH_INPUTS,
        description=(
            "The rows to run. The deployment's MAX_BATCH_ROWS, reported by "
            "GET /instance, is the operational cap and is usually lower."
        ),
    )
    engines: list[str] | None = ENGINES_FIELD
    k: int = K_FIELD
    ensemble_method: FusionMethodName = ENSEMBLE_METHOD_FIELD
    flag_radius_km: float = FLAG_RADIUS_FIELD
    on_row_error: RowErrorPolicy = ON_ROW_ERROR_FIELD

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "inputs": [
                        {
                            "id": "1",
                            "post": "Queue at the Bedok hawker centre again",
                            "ground_truth_city": "Bedok",
                        }
                    ],
                    "k": 5,
                    "on_row_error": "skip",
                }
            ]
        },
    )


class BatchPredictionRow(BaseModel):
    id: str
    status: RowStatusName
    in_catalogue: bool
    catalogue_sha: str | None = Field(
        default=None,
        description=(
            "Hash of the catalogue this row was run against, the same value "
            "the run manifest carries. It is on the row so an export can "
            "record what each prediction was chosen from without a join."
        ),
    )
    # The row's own difficulty bucket, the same value the per-difficulty
    # table groups by, echoed back so an export can carry it without the
    # client having to re-read the uploaded file.
    bucket: str | None = None
    ground_truth_city: str | None = None
    ground_truth_user_city: str | None = None
    ground_truth_place_id: str | None = None
    ground_truth_user_place_id: str | None = None
    per_engine: dict[str, EnginePrediction] = {}
    ensembles: dict[str, EnsembleView] = {}
    triangulation: TriangulationView | None = None
    error: str | None = Field(
        default=None,
        description=(
            "Why this row was not run. Only set with `status: \"error\"`, "
            "which a row can only reach when `on_row_error` is `skip`."
        ),
    )


class EngineMetricsView(BaseModel):
    name: str
    acc_at_1: float
    acc_at_5: float
    acc_at_1_ci: tuple[float, float]
    acc_at_5_ci: tuple[float, float]
    mean_rank: float = Field(
        description=(
            "Mean rank of the truth in the top-k. A row where the truth is not "
            "in the list at all contributes the sentinel 1,000,000, so an "
            "engine that abstained throughout once reported a mean rank of "
            "800,000.4. The field is kept for compatibility; read "
            "`mean_rank_found` and `n_rank_found` instead."
        )
    )
    mean_rank_found: float = Field(
        default=0.0,
        description="Mean rank over the rows where the truth was in the list at all.",
    )
    n_rank_found: int = Field(
        default=0, description="How many rows `mean_rank_found` was computed over."
    )
    median_error_km: float
    mean_error_km: float
    acc_at_161km: float
    n_geo: int
    n_abstained: int = 0
    median_latency_ms: float
    total_cost_usd: float = Field(
        description="Estimated from the listed prices in the run manifest, not from a bill."
    )
    n_evaluated: int


class EnsembleMetricsView(BaseModel):
    granularity: GranularityName
    acc_at_1: float
    acc_at_5: float
    acc_at_1_ci: tuple[float, float]
    acc_at_5_ci: tuple[float, float] = (0.0, 0.0)
    mean_rank: float = Field(
        description=(
            "Carries the same 1,000,000 sentinel as the per-engine field; read "
            "`mean_rank_found` and `n_rank_found` instead."
        )
    )
    mean_rank_found: float = 0.0
    n_rank_found: int = 0
    median_error_km: float
    acc_at_161km: float
    n_geo: int = 0
    n_evaluated: int
    differs_from_best_single_rate: float


class BucketMetricsView(BaseModel):
    bucket: str
    n_rows: int
    acc_at_1: dict[str, float] = {}


class BannerMetricsView(BaseModel):
    n_labelled: int
    n_with_timeline: int = 0
    n_positive: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float


class EvalSummaryView(BaseModel):
    total_rows: int
    evaluated_rows: int
    ooc_rows: int
    error_rows: int
    catalogue_size: int
    per_engine: dict[str, EngineMetricsView] = {}
    ensembles: dict[str, EnsembleMetricsView] = {}
    per_bucket: dict[str, BucketMetricsView] = {}
    banner: BannerMetricsView | None = None


class CataloguePlaceView(BaseModel):
    name: str
    place_id: str = Field(
        description=(
            "Opaque stable identifier, derived from the normalised name. The "
            "same place has the same identifier on every instance and across "
            "a restart."
        )
    )
    lat: float | None = None
    lon: float | None = None
    feature_type: FeatureTypeName | None = Field(
        default=None,
        description=(
            "What scale the place is. Null for an onboarded place: the "
            "operator gives a coordinate but nothing records the scale."
        ),
    )
    centroid_source: str | None = Field(
        default=None,
        description=(
            "Where the stored point came from. `wnut2016-gold-city-centre` for "
            "the 28 places added for the WNUT-2016 evaluation, "
            "`seed-approximate` for the 22 the catalogue started with, whose "
            "points were entered by hand with no source recorded. Null for an "
            "onboarded place, whose point the operator supplied."
        ),
    )
    source: CatalogueSourceName = "built-in"
    expires_in_minutes: float | None = None
    # How long an onboarded place has been in the shared catalogue. Shown on
    # the main view, not only inside the collapsed candidate list.
    age_minutes: float | None = None


class CatalogueView(BaseModel):
    size: int
    places: list[CataloguePlaceView]
    onboarded_count: int
    onboarded_cap: int
    onboarding_ttl_minutes: float
    distance_method: str = Field(
        default=DISTANCE_METHOD,
        description="How a distance between two of these points is measured.",
    )


class CityCountView(BaseModel):
    city: str
    post_count: int
    user_count: int
    # The same counts split by whether the gazetteer found a catalogue place
    # in the row's text at that level.
    post_named: int = 0
    post_unnamed: int = 0
    user_named: int = 0
    user_unnamed: int = 0


class BatchResponse(BaseModel):
    rows: list[BatchPredictionRow]
    summary: EvalSummaryView | None = None  # only set for /eval; null for /batch_predict
    rollup: list[CityCountView] = []  # per-place predicted-location counts (no ground truth needed)
    place_coordinates: dict[str, list[float]] = Field(
        default={}, description="Place name to [lat, lon], for every place named here."
    )
    places: list[PlaceRef] = Field(
        default=[],
        description=(
            "The same places as `place_coordinates`, each with its `place_id`, "
            "its feature type and where its stored point came from. A per-row "
            "or per-feature export can be built from `rows` and this list "
            "without a join on place names."
        ),
    )
    warnings: list[str] = Field(
        default=[],
        description="What the caller should know about the run as a whole.",
    )
    spend: SpendView | None = None
    manifest: RunManifestView | None = None


# ----- Declared views for the onboarding responses ---------------------------

class ProfileView(BaseModel):
    """A drafted or edited place profile, as the three /onboard methods return it."""

    name: str
    place_id: str
    aliases: list[str] = []
    landmarks: list[str] = []
    foods: list[str] = []
    slang: list[str] = []
    notes: str = ""
    lat: float | None = None
    lon: float | None = None
    region: str = ""
    source: ProfileSourceName = "stub"
    warnings: list[str] = Field(
        default=[],
        description="What the operator should fix before relying on the profile.",
    )


class CatalogueChangeView(BaseModel):
    """What a call did to the shared catalogue."""

    catalogue_status: Literal[
        "added",
        "already_present",
        "removed",
        "not_present",
        "built_in_restored",
        "expired",
        "evicted",
    ]
    catalogue_note: str
    catalogue_size: int
    profile_removed: bool = False
    onboarded_count: int = 0
    onboarded_cap: int = 0
    onboarding_ttl_minutes: float = 0.0
    expires_in_minutes: float | None = None
    evicted: list[str] = []


class OnboardResponse(ProfileView, CatalogueChangeView):
    """A drafted or saved profile, plus what it did to the catalogue."""

    edit_token: str | None = Field(
        default=None,
        description=(
            "The token that may later edit or remove this place, returned by "
            "`POST /onboard` alone. Null on a save. Keep it: when the "
            "instance sets GEOLENS_REQUIRE_EDIT_TOKEN, `PUT` and `DELETE "
            "/onboard` refuse without it."
        ),
    )


class ResetResponse(CatalogueChangeView):
    name: str
    place_id: str


class OnboardedPlaceView(BaseModel):
    name: str
    place_id: str
    expires_in_minutes: float | None = None


class OnboardingStatusView(BaseModel):
    onboarded_count: int
    onboarded_cap: int = Field(description="0 means no cap.")
    onboarding_ttl_minutes: float = Field(description="0 means onboarded places never expire.")
    onboarded: list[OnboardedPlaceView] = []
    catalogue_size: int


class EngineInfoView(BaseModel):
    """What a client needs to describe one engine on screen."""

    label: str
    family: str
    granularity: GranularityName
    tag: str = Field(description="The short pill the interface prints beside the engine's name.")
    tag_title: str = Field(description="The tooltip behind `tag`.")
    calls_a_third_party: bool = Field(
        description="Whether a call sends the input text off this server."
    )


class BudgetView(BaseModel):
    per_hour: int = Field(description="0 means no limit, and no X-RateLimit header is sent.")
    remaining: int | None = Field(
        default=None, description="Null when the budget is unlimited."
    )


class InstanceView(BaseModel):
    """What this deployment enforces, and what the verification flag measured."""

    version: str
    api_version: str
    git_commit: str | None = None
    require_region: bool
    require_edit_token: bool = Field(
        default=False,
        description=(
            "Whether `PUT` and `DELETE /onboard` need the `edit_token` that "
            "`POST /onboard` returned for the place."
        ),
    )
    refuse_address_like: bool = Field(
        default=False,
        description=(
            "Whether a name that reads as a building or a street address is "
            "refused. Otherwise it is warned about."
        ),
    )
    engines: dict[str, EngineInfoView]
    local_engines: list[str] = Field(
        description="The engines that send no text to a third party, in roster order."
    )
    limits: dict[str, BudgetView]
    spend: SpendView
    max_batch_rows: int
    max_batch_bytes: int
    max_batch_chars: int = MAX_BATCH_CHARS
    max_post_chars: int = Field(
        default=MAX_POST_CHARS,
        description="The longest post, and the longest entry of a timeline.",
    )
    max_timeline_chars: int = Field(
        default=MAX_TIMELINE_CHARS,
        description="The longest timeline, over all of its entries.",
    )
    distance_method: str = DISTANCE_METHOD
    flag_radius_km_default: float = ACC_KM_THRESHOLD
    flag_reference: dict[str, Any] = Field(
        description="What the verification flag measured on WNUT-2016, from the committed rescore."
    )
    engine_reference: dict[str, Any] = Field(
        default={},
        description=(
            "Each engine's Acc@1 on the committed WNUT-2016 run, with its 95% "
            "Wilson interval and the rows behind it, split for the post-level "
            "engines by whether the post named a catalogue place. It describes "
            "that benchmark, not this instance; `provenance` says so."
        ),
    )


class HealthView(BaseModel):
    status: Literal["ok"] = "ok"
    built_in_places_present: bool = Field(
        default=True,
        description=(
            "Whether the live catalogue still holds all 50 built-in places. "
            "False means an engine cannot answer with one of them and the "
            "instance should be restarted."
        ),
    )
    missing_built_in: list[str] = Field(
        default=[], description="The built-in places the catalogue does not hold."
    )


class ErrorBody(BaseModel):
    code: str
    message: str
    field: str | None = None
    details: list[dict[str, Any]] = []


class ErrorResponse(BaseModel):
    """The one shape every error takes, on every endpoint."""

    error: ErrorBody
    detail: str = Field(description="The message again, as a plain string.")


def _err(status: int, description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


# The status codes each family of endpoints can return, so the OpenAPI
# document lists what the server actually emits rather than 200 and 422.
COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    422: _err(422, "A request field is missing, unknown or outside its bounds."),
    500: _err(500, "The instance could not complete the request."),
}
INFERENCE_ERRORS: dict[int | str, dict[str, Any]] = {
    **COMMON_ERRORS,
    429: _err(429, "A per-address budget is spent. Retry-After says how long to wait."),
}
BATCH_ERRORS: dict[int | str, dict[str, Any]] = {
    **INFERENCE_ERRORS,
    400: _err(400, "The upload could not be read as CSV."),
    413: _err(413, "The request carries more rows or more bytes than the instance accepts."),
}
ONBOARD_ERRORS: dict[int | str, dict[str, Any]] = {
    **INFERENCE_ERRORS,
    403: _err(403, "The edit token for this place is missing or wrong."),
    404: _err(404, "No such place in the catalogue."),
    409: _err(409, "The catalogue already holds this place under another spelling."),
}


# ----- Shared view builders --------------------------------------------------

def _ensemble_view(er: Any) -> EnsembleView:
    return EnsembleView(
        granularity=er.granularity,
        consensus_city=er.consensus_city,
        consensus_place_id=place_id(er.consensus_city) if er.consensus_city else None,
        consensus_confidence=er.consensus_confidence,
        method=er.method,
        top_k=er.top_k,
        contributing_engines=er.contributing_engines,
        best_single_engine=er.best_single_engine,
        best_single_city=er.best_single_city,
        best_single_confidence=er.best_single_confidence,
        delta_vs_best_single=er.delta_vs_best_single,
        differs_from_best_single=er.differs_from_best_single,
    )


def _triangulation_view(tri: Any) -> TriangulationView:
    return TriangulationView(
        consensus_city=tri.consensus_city,
        consensus_confidence=tri.consensus_confidence,
        agreement_score=tri.agreement_score,
        disagreement_flag=tri.disagreement_flag,
        post_consensus_city=tri.post_consensus_city,
        user_consensus_city=tri.user_consensus_city,
        post_consensus_place_id=(
            place_id(tri.post_consensus_city) if tri.post_consensus_city else None
        ),
        user_consensus_place_id=(
            place_id(tri.user_consensus_city) if tri.user_consensus_city else None
        ),
        disagreement_km=tri.disagreement_km,
        disagreement_score=tri.disagreement_score,
        post_consensus_votes=tri.post_consensus_votes,
        post_consensus_answered=tri.post_consensus_answered,
        user_consensus_votes=tri.user_consensus_votes,
        user_consensus_answered=tri.user_consensus_answered,
        notes=tri.notes,
    )


def _single_query_abstentions(per_engine: dict[str, Prediction]) -> dict[str, int]:
    """Whether the one query named a catalogue place, at each level.

    The batch manifest has carried these counts since the placeless rows
    were split out; a single query reported neither, so a reader of one
    exported manifest could not tell an abstention from a missing engine.
    """
    out: dict[str, int] = {}
    for level, name in (("post", "gazetteer_post"), ("user", "gazetteer_user")):
        pred = per_engine.get(name)
        out[level] = 1 if (pred is not None and pred.usable and pred.abstain) else 0
    return out


def _single_query_matches(per_engine: dict[str, Prediction]) -> dict[str, int]:
    out: dict[str, int] = {}
    for level, name in (("post", "gazetteer_post"), ("user", "gazetteer_user")):
        pred = per_engine.get(name)
        out[level] = 1 if (pred is not None and pred.usable and not pred.abstain) else 0
    return out


def _row_error(predicted: str, truth: str | None) -> tuple[float | None, bool | None]:
    """(error_km, within_161km) for one engine's answer against one truth.

    Both are None when there is no truth, when the engine named no place, or
    when either place has no coordinate: an answer that cannot be put on the
    map is not a near miss.
    """
    if not predicted or not truth:
        return None, None
    a = coords_for(predicted)
    b = coords_for(truth)
    if a is None or b is None:
        return None, None
    km = haversine_km(a, b)
    return round(km, 1), km <= ACC_KM_THRESHOLD


def request_base_url(request: Request) -> str:
    """The URL a client reached this instance on, with the scheme it used.

    Behind the hosted proxy `request.base_url` reports http for a request
    that arrived over https, because TLS ends at the proxy. The manifest is
    a provenance record, so it says what the caller actually used.
    """
    base = str(request.base_url)
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if trusted_proxy_hops() > 0 and forwarded in ("http", "https"):
        scheme, _, rest = base.partition("://")
        if rest and scheme != forwarded:
            return f"{forwarded}://{rest}"
    return base


# ----- App -------------------------------------------------------------------

API_DESCRIPTION = f"""
GeoLens runs a roster of geolocation engines on one input, fuses them at each
level, and raises a verification flag when the post-level and user-level
consensus places are far apart.

**Stability.** The HTTP contract carries its own version, `{API_VERSION}`,
reported by `GET /instance` as `api_version` and in every run manifest. It is
separate from the build version. Within one major version a field may be
added and an optional parameter may appear, but no field is removed or
renamed and no status code changes what it means. `CHANGELOG.md` records what
has moved. Anything not described here, including the exact wording of a
warning or an evidence string, may change in any release.

**Errors.** Every error, from every endpoint, is
`{{"error": {{"code", "message", "field", "details"}}, "detail": "..."}}`.
`detail` repeats the message as a plain string.

**Distances.** Every distance this API reports is measured by {DISTANCE_METHOD}.

**Costs.** `cost_usd` is estimated from the listed per-token prices recorded
in each run manifest under `price_table`. It is not read from a bill, and a
model whose price is not listed reports no cost rather than a wrong one.
""".strip()


def create_app() -> FastAPI:
    engines, catalogue = build_engines()
    granularities = {name: e.granularity for name, e in engines.items()}
    onboarded = OnboardingRegistry(catalogue)
    catalogue_lock = CatalogueLock()
    spend = SpendLedger()
    stop_reconciling = threading.Event()

    def _reconcile_loop() -> None:
        interval = reconcile_seconds()
        while not stop_reconciling.wait(interval):
            with catalogue_lock.write():
                removed = onboarded.expire()
                discarded = onboarded.reconcile()
            if removed or discarded:
                logger.info(
                    "reconciled the profile cache: %d expired, %d discarded",
                    len(removed),
                    len(discarded),
                )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        mode = "placeholder" if os.getenv("GEOLENS_STUB_MODE", "0") == "1" else "real"
        logger.info(
            "GeoLens %s starting: inference mode %s, %d catalogue places, "
            "trusted proxy hops %d",
            __version__,
            mode,
            len(catalogue),
            trusted_proxy_hops(),
        )
        with catalogue_lock.write():
            discarded = onboarded.reconcile()
        if discarded:
            logger.info("discarded %d stored profile(s) left from an earlier run", len(discarded))
        interval = reconcile_seconds()
        thread: threading.Thread | None = None
        if interval > 0:
            thread = threading.Thread(
                target=_reconcile_loop, name="geolens-reconcile", daemon=True
            )
            thread.start()
        try:
            yield
        finally:
            stop_reconciling.set()
            if thread is not None:
                thread.join(timeout=1.0)

    app = FastAPI(
        title="GeoLens",
        version=__version__,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        servers=[
            {"url": "https://kwanhui-geo-lens.hf.space", "description": "Hosted instance"},
            {"url": "http://localhost:7860", "description": "A local run (make demo)"},
        ],
    )
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        # The quota headers are what the page reads to show how much of each
        # budget is left; without this a cross-origin client cannot see them.
        expose_headers=["*"],
    )

    @app.middleware("http")
    async def _cap_json_body(request: Request, call_next: Any) -> Any:
        """Refuse a JSON batch body larger than the instance's byte cap.

        The CSV endpoints read the upload and measure it; a JSON body is
        parsed before the handler runs, so the length the client declared is
        checked here. A body that declares no length is bounded by the
        total-character cap `_validate_inputs` applies instead.
        """
        if request.method == "POST" and request.url.path in JSON_BATCH_PATHS:
            declared = request.headers.get("content-length")
            cap = max_batch_bytes()
            if declared and declared.isdigit() and int(declared) > cap:
                from geolens.ui.errors import envelope

                return envelope(
                    413,
                    f"Request body too large: {int(declared)} bytes (max {cap}).",
                    code="body_too_large",
                    field="inputs",
                )
        return await call_next(request)

    buckets: dict[str, dict[str, deque[float]]] = {
        "queries": defaultdict(deque),
        "batches": defaultdict(deque),
        "profiles": defaultdict(deque),
    }
    caps = {
        "queries": max_queries_per_hour,
        "batches": max_batches_per_hour,
        "profiles": max_profile_saves_per_hour,
    }
    what_counts = {
        "queries": "queries",
        "batches": "bulk runs",
        "profiles": "profile saves and deletions",
    }

    # What an operator or a test needs to inspect without reaching into a
    # closure: the live catalogue, who holds it, the per-address windows and
    # the spend ledger.
    app.state.catalogue = catalogue
    app.state.onboarded = onboarded
    app.state.rate_limit_buckets = buckets
    app.state.spend = spend

    def _sweep(kind: str, now: float) -> None:
        """Drop the calls that have aged out, and every address left empty.

        An address is remembered only while it has a call inside the window;
        an empty bucket is deleted rather than kept as a record that someone
        was here.
        """
        empty: list[str] = []
        for ip, bucket in buckets[kind].items():
            while bucket and bucket[0] < now - 3600:
                bucket.popleft()
            if not bucket:
                empty.append(ip)
        for ip in empty:
            del buckets[kind][ip]

    def _remaining(kind: str, ip: str, now: float) -> int | None:
        """How many calls are left in this budget, or None when it is unlimited.

        An unlimited budget reports None rather than 0, which a client reads
        as exhausted.
        """
        cap = caps[kind]()
        if cap <= 0:
            return None
        _sweep(kind, now)
        return max(0, cap - len(buckets[kind].get(ip, ())))

    def _quota_header_values(request: Request) -> dict[str, str]:
        """The X-RateLimit headers for the budgets this instance actually applies."""
        ip = client_address(request)
        now = time.time()
        headers: dict[str, str] = {}
        for kind in buckets:
            remaining = _remaining(kind, ip, now)
            if remaining is None:
                # No header at all for an unlimited budget: `Remaining: 0`
                # reads to a client as out of quota.
                continue
            headers[f"X-RateLimit-Limit-{kind.capitalize()}"] = str(caps[kind]())
            headers[f"X-RateLimit-Remaining-{kind.capitalize()}"] = str(remaining)
        return headers

    def _quota_headers(request: Request, response: Response | None) -> None:
        """Say how much of each budget is left, so a limit is visible before it bites."""
        if response is None:
            return
        response.headers.update(_quota_header_values(request))

    def _rate_limit(kind: str, request: Request, response: Response | None = None) -> None:
        """Count one call against a budget, or raise 429 with how long to wait."""
        _warn_about_missing_forwarded_header(request)
        cap = caps[kind]()
        if cap > 0:
            ip = client_address(request)
            now = time.time()
            _sweep(kind, now)
            bucket = buckets[kind][ip]
            if len(bucket) >= cap:
                # The wait is until the oldest call in the window ages out,
                # which is when a slot frees, not the length of the window.
                retry_after = max(1, int(bucket[0] + 3600 - now) + 1)
                minutes = max(1, round(retry_after / 60))
                headers = _quota_header_values(request)
                headers["Retry-After"] = str(retry_after)
                raise ApiError(
                    429,
                    (
                        f"Per-address limit reached: {cap} {what_counts[kind]} per hour on this "
                        f"instance. Try again in about {minutes} "
                        f"{'minute' if minutes == 1 else 'minutes'}."
                    ),
                    code="rate_limited",
                    field=kind,
                    headers=headers,
                )
            bucket.append(now)
        _quota_headers(request, response)

    def _selected_engines(names: list[str] | None) -> list[str] | None:
        """Validate a requested engine list against the registry."""
        if names is None:
            return None
        unknown = [n for n in names if n not in engines]
        if unknown:
            raise ApiError(
                422,
                (
                    f"Unknown engine name(s): {', '.join(sorted(unknown))}. "
                    f"The registry holds {', '.join(engines)}."
                ),
                code="unknown_engine",
                field="engines",
            )
        if not names:
            raise ApiError(
                422,
                "engines is empty. Omit it to run every engine, or name at least one.",
                code="empty_engine_list",
                field="engines",
            )
        return list(dict.fromkeys(names))

    def _coordinates(names: list[str]) -> dict[str, list[float]]:
        """Coordinates for the places named in a response, from the shared catalogue."""
        out: dict[str, list[float]] = {}
        for name in names:
            if not name or name in out:
                continue
            coords = coords_for(name)
            if coords is not None:
                out[name] = [coords[0], coords[1]]
        return out

    def _place_refs(names: list[str]) -> list[PlaceRef]:
        """Every place named in a response, with its identifier and its point.

        An export needs a coordinate and a stable key per place, so a GIS
        reader does not have to join an engine's answer back to a coordinate
        on the place name.
        """
        seen: set[str] = set()
        refs: list[PlaceRef] = []
        for name in names:
            if not name:
                continue
            key = name_key(name)
            if key in seen:
                continue
            seen.add(key)
            coords = coords_for(name)
            feature_type, centroid_source = feature_for(name)
            refs.append(
                PlaceRef(
                    place_id=place_id(name),
                    name=name,
                    lat=None if coords is None else coords[0],
                    lon=None if coords is None else coords[1],
                    source="built-in" if is_default_city(name) else "onboarded",
                    feature_type=feature_type,  # type: ignore[arg-type]
                    centroid_source=centroid_source,
                )
            )
        return refs

    def _catalogue_ages() -> dict[str, float]:
        now = time.time()
        ages: dict[str, float] = {}
        for name in onboarded.names():
            added = onboarded.added_at(name)
            if added is not None:
                ages[name] = max(0.0, (now - added) / 60.0)
        return ages

    def _spend_view() -> SpendView:
        return SpendView(**spend.state().as_dict())

    def _require_edit_rights(request: Request, body_token: str, name: str) -> None:
        """Refuse an edit or a removal without the token the place was drafted with.

        The operator token, when the deployment sets one, passes everything:
        it is how a place nobody holds the edit token for is purged.
        """
        supplied = (
            request.headers.get("x-geolens-edit-token") or body_token or ""
        ).strip()
        operator = operator_token()
        if operator:
            header = (request.headers.get("x-geolens-operator-token") or "").strip()
            if header and secrets.compare_digest(header, operator):
                return
        if not require_edit_token():
            return
        if onboarded.token_matches(name, supplied):
            return
        raise ApiError(
            403,
            (
                f"Editing or removing {name!r} on this instance needs the "
                "edit_token that POST /onboard returned for it. Send it as "
                "the edit_token field or the X-GeoLens-Edit-Token header, or "
                "draft the place again to get a new one."
            ),
            code="edit_token_required" if not supplied else "edit_token_invalid",
            field="edit_token",
        )

    def _log_onboarding(request: Request, name: str, region: str) -> None:
        """Record that a place was onboarded, with the address hashed."""
        logger.info(
            "onboarded place_id=%s region=%s from=%s",
            place_id(name),
            region or "(none)",
            hashed_address(client_address(request)),
        )

    def _engines_within_spend(selected: list[str] | None) -> list[str] | None:
        """`selected`, minus the paid engines while the spend ceiling is reached.

        The engines that run on this server keep answering; the ones that
        call a paid model come back skipped, carrying the reason.
        """
        if not spend.state().ceiling_reached:
            return selected
        names = list(engines) if selected is None else list(selected)
        return [n for n in names if not engines[n].calls_a_third_party]

    def _run_within_spend(
        payload: GeolocateInput, *, k: int, selected: list[str] | None
    ) -> dict[str, Prediction]:
        """Run the engines the ceiling allows, and record what they cost."""
        allowed = _engines_within_spend(selected)
        per_engine = run_engines(engines, payload, k=k, selected=allowed)
        if allowed is not None and allowed != selected:
            asked = list(engines) if selected is None else selected
            for name in asked:
                if name not in allowed:
                    per_engine[name] = skipped_prediction(name, reason=SPEND_CEILING_SKIP)
        spend.record(sum(p.cost_usd for p in per_engine.values()))
        return per_engine

    @app.get("/healthz", response_model=HealthView, responses=dict(COMMON_ERRORS))
    def healthz() -> HealthView:
        """Liveness, plus whether the catalogue still holds every built-in place."""
        missing = onboarded.missing_built_in()
        return HealthView(
            status="ok",
            built_in_places_present=not missing,
            missing_built_in=missing,
        )

    @app.post(
        "/geolocate",
        response_model=GeolocateResponse,
        responses=dict(INFERENCE_ERRORS),
        summary="Run the engines on one input",
    )
    def geolocate(
        req: GeolocateRequest, request: Request, response: Response
    ) -> GeolocateResponse:
        selected = _selected_engines(req.engines)
        _rate_limit("queries", request, response)
        with catalogue_lock.write():
            onboarded.expire()
        if not req.post and not req.user_posts:
            raise ApiError(
                422,
                (
                    "Provide a post, a user timeline, or both. No engine reads "
                    "user_handle, so a handle on its own gives them nothing to run on."
                ),
                code="no_input_text",
                field="post",
            )
        payload = GeolocateInput(
            post=req.post,
            user_handle=req.user_handle,
            user_posts=req.user_posts,
        )
        # One catalogue for the whole request: both prompts are built from it
        # and the manifest hashes it, so an onboarding landing now waits.
        with catalogue_lock.read():
            per_engine = _run_within_spend(payload, k=req.k, selected=selected)
            catalogue_ages = _catalogue_ages()
            snapshot = list(catalogue)
        tri = triangulate(per_engine, engines=granularities, radius_km=req.flag_radius_km)

        ensembles: dict[str, EnsembleView] = {}
        for target in ("post", "user"):
            er = ensemble(per_engine, granularities, target=target, k=req.k,
                          method=as_fusion_method(req.ensemble_method))
            if er is None:
                continue
            ensembles[target] = _ensemble_view(er)

        return GeolocateResponse(
            per_engine={n: _engine_view(p) for n, p in per_engine.items()},
            triangulation=_triangulation_view(tri),
            ensembles=ensembles,
            place_coordinates=_coordinates(
                [e.consensus_city for e in ensembles.values()]
                + [tri.post_consensus_city, tri.user_consensus_city]
                + [p.city for p in per_engine.values()]
            ),
            places=_place_refs(
                [e.consensus_city for e in ensembles.values()]
                + [tri.post_consensus_city, tri.user_consensus_city]
                + [p.city for p in per_engine.values()]
            ),
            spend=_spend_view(),
            manifest=RunManifestView(
                **build_manifest(
                    engines,
                    snapshot,
                    k=req.k,
                    ensemble_method=req.ensemble_method,
                    flag_radius_km=req.flag_radius_km,
                    predictions=per_engine,
                    counts=call_counts([per_engine]),
                    selected_engines=selected,
                    input_sha256=input_fingerprint(
                        [{"post": req.post, "user_posts": req.user_posts}]
                    ),
                    input_row_count=1,
                    base_url=request_base_url(request),
                    catalogue_ages_minutes=catalogue_ages,
                    gazetteer_abstentions=_single_query_abstentions(per_engine),
                    gazetteer_matches=_single_query_matches(per_engine),
                )
            ),
        )

    @app.post(
        "/onboard",
        response_model=OnboardResponse,
        responses=dict(ONBOARD_ERRORS),
        summary="Draft a profile for a place and add it to the catalogue",
    )
    def onboard(
        req: OnboardRequest, request: Request, response: Response
    ) -> OnboardResponse:
        """Draft a profile for a place, add it, and return the token to edit it.

        The name is validated before it reaches the drafting prompt and the
        shared catalogue: it is listed verbatim in the instruction part of
        the prompt both classifiers send, so a name that reads as an
        instruction, wraps a place the catalogue already holds, or folds onto
        one under a different script is refused here.
        """
        # Drafting calls a model, so it counts against the inference budget.
        # Saving and deleting do not, and have their own.
        _rate_limit("queries", request, response)
        with catalogue_lock.write():
            onboarded.expire()
        try:
            city = validate_place_name(req.city)
            region = validate_region(req.region)
        except ProfileError as e:
            raise ApiError(422, str(e), code="invalid_profile_field", field="city") from e
        _check_place_scale(city)
        if require_region() and not region:
            raise ApiError(
                422,
                (
                    f"This instance requires a country or region for {city!r}, because "
                    "a bare place name is often ambiguous and the drafting model "
                    "resolves the ambiguity without reporting it. Add a hint such as "
                    "'Singapore' or 'West Kalimantan, Indonesia'."
                ),
                code="region_required",
                field="region",
            )
        # One place, one name. A spelling that differs from the catalogue's
        # only in case, in Unicode composition, or in the script a letter was
        # taken from is the same place, and adding it would put a second
        # entry with a second profile beside the first for every later
        # visitor.
        existing = find_in_catalogue(city, catalogue) or confusable_catalogue_place(
            city, catalogue
        )
        if existing is not None and existing != city:
            raise ApiError(
                409,
                (
                    f"The catalogue already holds this place as {existing!r}. "
                    f"Onboard or edit it under that spelling; {city!r} would add a "
                    "second entry for the same place."
                ),
                code="place_already_in_catalogue",
                field="city",
                details=[{"field": "city", "existing_name": existing,
                          "place_id": place_id(existing)}],
            )
        swallowed = swallowed_catalogue_place(city, catalogue)
        if swallowed is not None:
            raise ApiError(
                422,
                (
                    f"{city!r} contains the catalogue place {swallowed!r} without "
                    "extending it. Every catalogue name is listed in the prompt "
                    "the LLM classifiers send, so a name that wraps another "
                    "place in text is refused."
                ),
                code="name_contains_catalogue_place",
                field="city",
                details=[{"field": "city", "existing_name": swallowed}],
            )
        # A place someone else onboarded keeps its owner's profile and token:
        # a second caller gets the stored profile back and no token.
        supplied = (request.headers.get("x-geolens-edit-token") or "").strip()
        held = onboarded.token_for(city) is not None
        owner = (
            not held or not require_edit_token() or onboarded.token_matches(city, supplied)
        )
        kept = cached_profile(city) if held and not owner else None
        profile = kept or onboard_city(
            city, region=region, force_refresh=req.force_refresh and owner
        )
        token: str | None
        with catalogue_lock.write():
            change = onboarded.register(profile.name)
            if not held:
                token = onboarded.issue_token(profile.name)
            else:
                token = onboarded.token_for(profile.name) if owner else None
        _log_onboarding(request, profile.name, region)
        body = _profile_to_dict(profile, catalogue) | change.as_dict()
        return OnboardResponse(**body, edit_token=token)

    @app.put(
        "/onboard",
        response_model=OnboardResponse,
        responses=dict(ONBOARD_ERRORS),
        summary="Save the operator's edits to a profile",
    )
    def save_onboarded(
        req: SaveProfileRequest, request: Request, response: Response
    ) -> OnboardResponse:
        """Persist an edited place profile (the operator's edits in the interface).

        This edits; it does not create. A name that is neither onboarded nor
        built in is a 404, and creating a place goes through `POST /onboard`,
        which runs the region check and counts against the inference budget.

        Everything saved here reaches every other visitor of a hosted
        instance, so the fields are bounded before they are written: the
        gazetteer counts every alias and landmark against every post.

        Correcting a bad draft must not depend on the inference budget, so
        this draws on the profile budget instead.
        """
        _rate_limit("profiles", request, response)
        with catalogue_lock.write():
            onboarded.expire()
        try:
            name = validate_place_name(req.name)
        except ProfileError as e:
            raise ApiError(422, str(e), code="invalid_profile_field", field="name") from e

        present = find_in_catalogue(name, catalogue)
        if present is None:
            raise ApiError(
                404,
                (
                    f"{name!r} is not in the catalogue, and PUT /onboard edits a "
                    "profile rather than creating one. Draft it with POST /onboard "
                    "first, which runs the region check and counts against the "
                    "inference budget, then save the edits here."
                ),
                code="place_not_in_catalogue",
                field="name",
            )
        # Edit the place under the spelling the catalogue holds, so a save
        # cannot fork one place into two.
        name = present
        _require_edit_rights(request, req.edit_token, name)

        # The region check runs on every save, against the hint stored with
        # the profile when the request carries none, so a swapped latitude
        # and longitude is caught on a save that sends no region.
        stored = cached_profile(name)
        try:
            region = validate_region(req.region)
        except ProfileError as e:
            raise ApiError(422, str(e), code="invalid_profile_field", field="region") from e
        if not region and stored is not None:
            region = stored.region.strip()

        try:
            profile = CityProfile(
                name=name,
                aliases=validate_list("aliases", req.aliases, name),
                landmarks=validate_list("landmarks", req.landmarks, name),
                foods=validate_list("foods", req.foods),
                slang=validate_list("slang", req.slang),
                notes=validate_notes(req.notes),
                lat=req.lat,
                lon=req.lon,
                region=region,
                source="edited",
            )
            validate_coordinate(req.lat, req.lon)
        except ProfileError as e:
            raise ApiError(422, str(e), code="invalid_profile_field") from e

        saved = save_profile(profile)
        with catalogue_lock.write():
            change = onboarded.register(saved.name)
        return OnboardResponse(**(_profile_to_dict(saved, catalogue) | change.as_dict()))

    @app.delete(
        "/onboard",
        response_model=ResetResponse,
        responses=dict(ONBOARD_ERRORS),
        summary="Remove an onboarded place, or clear a built-in place's overlay",
    )
    def reset_onboarded(
        req: ResetRequest, request: Request, response: Response
    ) -> ResetResponse:
        """Undo an onboarding, so a cold-start scenario can be shown again.

        A hosted instance shares one catalogue, so whoever runs a scenario
        first leaves the place onboarded for everyone after them. The
        scenario tiles call this before they run. A built-in catalogue city is
        never dropped from the catalogue, because that would change what every
        engine can predict; its drafted profile is an overlay, and this
        discards the overlay and puts the built-in profile back.

        Removing a bad draft is a correction, so it draws on the profile
        budget rather than the inference budget.
        """
        _rate_limit("profiles", request, response)
        try:
            city = validate_place_name(req.city)
        except ProfileError as e:
            raise ApiError(422, str(e), code="invalid_profile_field", field="city") from e
        # Removing a place nobody holds needs no rights: there is nothing to protect.
        if onboarded.token_for(city) is not None:
            _require_edit_rights(request, req.edit_token, city)
        with catalogue_lock.write():
            change = onboarded.reset(city)
        return ResetResponse(name=city, place_id=place_id(city), **change.as_dict())

    @app.get(
        "/catalogue",
        response_model=CatalogueView,
        responses=dict(COMMON_ERRORS),
        summary="Every place an engine may answer with",
    )
    def catalogue_listing() -> CatalogueView:
        """Every place an engine may answer with, and where each came from.

        The catalogue is closed, so an input naming a place outside it is
        answered with a place inside it; this is the list that answer is
        drawn from.
        """
        with catalogue_lock.write():
            onboarded.expire()
        state = onboarded.status()
        ages = _catalogue_ages()
        places = []
        for name in list(catalogue):
            coords = coords_for(name)
            built_in = is_default_city(name)
            feature_type, centroid_source = feature_for(name)
            places.append(
                CataloguePlaceView(
                    name=name,
                    place_id=place_id(name),
                    lat=None if coords is None else coords[0],
                    lon=None if coords is None else coords[1],
                    feature_type=feature_type,  # type: ignore[arg-type]
                    centroid_source=centroid_source,
                    source="built-in" if built_in else "onboarded",
                    expires_in_minutes=onboarded.expires_in_minutes(name),
                    age_minutes=ages.get(name),
                )
            )
        return CatalogueView(
            size=len(catalogue),
            places=places,
            onboarded_count=state["onboarded_count"],
            onboarded_cap=state["onboarded_cap"],
            onboarding_ttl_minutes=state["onboarding_ttl_minutes"],
        )

    @app.get(
        "/onboard/status",
        response_model=OnboardingStatusView,
        responses=dict(COMMON_ERRORS),
        summary="What is onboarded now, the cap, and the expiries",
    )
    def onboarding_status() -> OnboardingStatusView:
        """How many places are onboarded, the cap, and when each expires."""
        with catalogue_lock.write():
            onboarded.expire()
        state = onboarded.status()
        return OnboardingStatusView(
            onboarded_count=state["onboarded_count"],
            onboarded_cap=state["onboarded_cap"],
            onboarding_ttl_minutes=state["onboarding_ttl_minutes"],
            onboarded=[
                OnboardedPlaceView(
                    name=entry["name"],
                    place_id=place_id(entry["name"]),
                    expires_in_minutes=entry["expires_in_minutes"],
                )
                for entry in state["onboarded"]
            ],
            catalogue_size=state["catalogue_size"],
        )

    @app.get(
        "/instance",
        response_model=InstanceView,
        responses=dict(COMMON_ERRORS),
        summary="What this deployment enforces, and what the flag measured",
    )
    def instance_settings(request: Request, response: Response) -> InstanceView:
        """What this deployment enforces, and what the flag measured.

        The page reads this rather than assuming: the region hint is optional
        on a local run and required on the hosted instance, the rate limits
        and the spend ceiling are off unless a deployment sets them, the
        flag's error rate and each engine's reference accuracy come from the
        committed evaluation rather than from a number typed into the page,
        and each engine's label, tag and tooltip come from the one roster.
        """
        ip = client_address(request)
        now = time.time()
        _quota_headers(request, response)
        meta = engine_metadata(engines)
        return InstanceView(
            version=__version__,
            api_version=API_VERSION,
            git_commit=git_commit(),
            require_region=require_region(),
            require_edit_token=require_edit_token(),
            refuse_address_like=refuse_address_like(),
            engines={name: EngineInfoView(**info) for name, info in meta.items()},
            local_engines=[
                name for name, info in meta.items() if not info["calls_a_third_party"]
            ],
            limits={
                kind: BudgetView(per_hour=caps[kind](), remaining=_remaining(kind, ip, now))
                for kind in buckets
            },
            spend=_spend_view(),
            max_batch_rows=max_batch_rows(),
            max_batch_bytes=max_batch_bytes(),
            flag_reference=flag_reference(),
            engine_reference=engine_reference(),
        )

    # ----- Batch endpoints ---------------------------------------------------

    def _run_and_view(
        rows_in: list[BatchInput],
        k: int,
        with_eval: bool,
        ensemble_method: str = "weighted",
        flag_radius_km: float = ACC_KM_THRESHOLD,
        selected: list[str] | None = None,
        base_url: str = "",
    ) -> BatchResponse:
        with catalogue_lock.write():
            onboarded.expire()
        # One catalogue for the whole run, as for a single query.
        with catalogue_lock.read():
            results = run_batch(
                rows_in,
                engines,
                catalogue=catalogue,
                k=k,
                ensemble_method=ensemble_method,
                flag_radius_km=flag_radius_km,
                selected=_engines_within_spend(selected),
            )
            catalogue_ages = _catalogue_ages()
            snapshot = list(catalogue)
        spend.record(
            sum(p.cost_usd for r in results for p in r.per_engine.values())
        )
        sha = catalogue_hash(snapshot)
        rows_out: list[BatchPredictionRow] = []
        for r in results:
            per_engine_view = {n: _engine_view(p) for n, p in r.per_engine.items()}
            # Score each engine's answer against the truth for the level it
            # answers at, so a mapping client has the distance per engine
            # without recomputing it from two coordinate lookups.
            for name, view in per_engine_view.items():
                truth = truth_for(r, granularities.get(name))
                view.error_km, view.within_161km = _row_error(view.city, truth)
            ens_view = {g: _ensemble_view(er) for g, er in r.ensembles.items()}
            tri_view = (
                None if r.triangulation is None else _triangulation_view(r.triangulation)
            )
            rows_out.append(
                BatchPredictionRow(
                    id=r.id,
                    status=r.status,
                    in_catalogue=r.in_catalogue,
                    catalogue_sha=sha,
                    bucket=bucket_of(r),
                    ground_truth_city=r.ground_truth_city,
                    ground_truth_user_city=r.ground_truth_user_city,
                    ground_truth_place_id=(
                        place_id(r.ground_truth_city) if r.ground_truth_city else None
                    ),
                    ground_truth_user_place_id=(
                        place_id(r.ground_truth_user_city)
                        if r.ground_truth_user_city
                        else None
                    ),
                    per_engine=per_engine_view,
                    ensembles=ens_view,
                    triangulation=tri_view,
                    error=r.error,
                )
            )

        summary_view: EvalSummaryView | None = None
        if with_eval:
            # Use the live catalogue size (built-ins + onboarded), not the
            # static default, so the summary's N matches the run manifest.
            s = compute_summary(
                results, catalogue_size=len(catalogue), granularities=granularities
            )
            summary_view = EvalSummaryView(
                total_rows=s.total_rows,
                evaluated_rows=s.evaluated_rows,
                ooc_rows=s.ooc_rows,
                error_rows=s.error_rows,
                catalogue_size=s.catalogue_size,
                per_engine={
                    n: EngineMetricsView(
                        name=m.name,
                        acc_at_1=m.acc_at_1,
                        acc_at_5=m.acc_at_5,
                        acc_at_1_ci=m.acc_at_1_ci,
                        acc_at_5_ci=m.acc_at_5_ci,
                        mean_rank=m.mean_rank,
                        mean_rank_found=m.mean_rank_found,
                        n_rank_found=m.n_rank_found,
                        median_error_km=m.median_error_km,
                        mean_error_km=m.mean_error_km,
                        acc_at_161km=m.acc_at_161km,
                        n_geo=m.n_geo,
                        n_abstained=m.n_abstained,
                        median_latency_ms=m.median_latency_ms,
                        total_cost_usd=m.total_cost_usd,
                        n_evaluated=m.n_evaluated,
                    )
                    for n, m in s.per_engine.items()
                },
                ensembles={
                    g: EnsembleMetricsView(
                        granularity=m.granularity,  # type: ignore[arg-type]
                        acc_at_1=m.acc_at_1,
                        acc_at_5=m.acc_at_5,
                        acc_at_1_ci=m.acc_at_1_ci,
                        acc_at_5_ci=m.acc_at_5_ci,
                        mean_rank=m.mean_rank,
                        mean_rank_found=m.mean_rank_found,
                        n_rank_found=m.n_rank_found,
                        median_error_km=m.median_error_km,
                        acc_at_161km=m.acc_at_161km,
                        n_geo=m.n_geo,
                        n_evaluated=m.n_evaluated,
                        differs_from_best_single_rate=m.differs_from_best_single_rate,
                    )
                    for g, m in s.ensembles.items()
                },
                per_bucket={
                    b: BucketMetricsView(bucket=bm.bucket, n_rows=bm.n_rows, acc_at_1=bm.acc_at_1)
                    for b, bm in s.per_bucket.items()
                },
                banner=(
                    BannerMetricsView(
                        n_labelled=s.banner.n_labelled,
                        n_with_timeline=s.banner.n_with_timeline,
                        n_positive=s.banner.n_positive,
                        true_positive=s.banner.true_positive,
                        false_positive=s.banner.false_positive,
                        false_negative=s.banner.false_negative,
                        precision=s.banner.precision,
                        recall=s.banner.recall,
                    )
                    if s.banner is not None
                    else None
                ),
            )

        manifest = build_manifest(
            engines,
            snapshot,
            k=k,
            ensemble_method=ensemble_method,
            flag_radius_km=flag_radius_km,
            counts=call_counts([r.per_engine for r in results if r.per_engine]),
            selected_engines=selected,
            input_sha256=input_fingerprint(
                [
                    {"id": r.id, "post": r.post, "user_posts": r.user_posts}
                    for r in rows_in
                ]
            ),
            input_row_count=len(rows_in),
            base_url=base_url or None,
            catalogue_ages_minutes=catalogue_ages,
            gazetteer_abstentions=gazetteer_abstentions(results),
            gazetteer_matches=gazetteer_matches(results),
        )
        rollup = [
            CityCountView(
                city=c.city,
                post_count=c.post_count,
                user_count=c.user_count,
                post_named=c.post_named,
                post_unnamed=c.post_unnamed,
                user_named=c.user_named,
                user_unnamed=c.user_unnamed,
            )
            for c in compute_rollup(results)
        ]
        named: list[str] = []
        for r in results:
            named.extend(e.consensus_city for e in r.ensembles.values())
            named.extend(p.city for p in r.per_engine.values())
            named.append(r.ground_truth_city or "")
            named.append(r.ground_truth_user_city or "")
            if r.triangulation is not None:
                named.append(r.triangulation.post_consensus_city)
                named.append(r.triangulation.user_consensus_city)
        return BatchResponse(
            rows=rows_out,
            summary=summary_view,
            rollup=rollup,
            place_coordinates=_coordinates(named),
            places=_place_refs(named),
            warnings=_handle_warning([r.id for r in rows_in]),
            spend=_spend_view(),
            manifest=RunManifestView(**manifest),
        )

    def _validate_inputs(inputs: list[BatchInput], on_row_error: str) -> list[BatchInput]:
        """Refuse what the instance cannot run, and mark what one row cannot.

        The size caps refuse the whole request whatever the policy: they are
        about what the instance will allocate, not about one bad row. A row
        with nothing to run on is a per-row problem, so `skip` marks it and
        the runner returns it with `status: "error"` beside the results.
        """
        if not inputs:
            raise ApiError(
                400, "No input rows.", code="empty_batch", field="inputs"
            )
        cap = max_batch_rows()
        if len(inputs) > cap:
            raise ApiError(
                413,
                f"Batch too large: {len(inputs)} rows (max {cap}).",
                code="too_many_rows",
                field="inputs",
            )
        total = sum(_row_chars(r) for r in inputs)
        if total > MAX_BATCH_CHARS:
            raise ApiError(
                413,
                (
                    f"Batch too large: {total} characters of text across "
                    f"{len(inputs)} rows (max {MAX_BATCH_CHARS})."
                ),
                code="too_many_characters",
                field="inputs",
            )
        out: list[BatchInput] = []
        for r in inputs:
            if r.error is None and not (r.post or r.user_posts):
                r.error = (
                    "the row has neither a post nor a user timeline, and no "
                    "engine reads user_handle on its own"
                )
            if r.error is not None and on_row_error == "fail":
                raise ApiError(
                    422,
                    f"Row id={r.id!r}: {r.error}. "
                    'Send on_row_error="skip" to process the other rows and '
                    "get this one back with an error.",
                    code="unusable_row",
                    field="inputs",
                    details=[{"field": "inputs", "id": r.id, "message": r.error}],
                )
            out.append(r)
        return out

    def _require_some_truth(inputs: list[BatchInput], where: str) -> None:
        """An /eval call has to carry a truth somewhere, or it measures nothing.

        A file whose truth header was missing or misspelt ran every engine,
        cost the caller the run, and came back 200 with an empty metrics
        table and nothing to say why.
        """
        if any(r.ground_truth_city or r.ground_truth_user_city for r in inputs):
            return
        raise ApiError(
            422,
            (
                f"No row carries a ground truth, so /eval has nothing to score. "
                f"{where} Use /batch_predict to run the engines without metrics."
            ),
            code="no_ground_truth",
            field="inputs",
        )

    @app.post(
        "/batch_predict",
        response_model=BatchResponse,
        responses=dict(BATCH_ERRORS),
        summary="Run the engines over many rows, with no ground truth",
    )
    def batch_predict(
        request: Request, response: Response, body: BatchRequest
    ) -> BatchResponse:
        selected = _selected_engines(body.engines)
        inputs = [
            BatchInput(
                id=r.id,
                post=r.post,
                user_posts=r.user_posts,
                user_handle=r.user_handle,
                ground_truth_city=None,  # batch_predict ignores ground truth
                bucket=r.bucket,
            )
            for r in body.inputs
        ]
        _reject_duplicate_ids(inputs)
        inputs = _validate_inputs(inputs, body.on_row_error)
        # Counted only once the request is known to be runnable, so a
        # refused upload costs the caller no slot.
        _rate_limit("batches", request, response)
        return _run_and_view(
            inputs,
            body.k,
            with_eval=False,
            ensemble_method=body.ensemble_method,
            flag_radius_km=body.flag_radius_km,
            selected=selected,
            base_url=request_base_url(request),
        )

    @app.post(
        "/batch_predict_csv",
        response_model=BatchResponse,
        responses=dict(BATCH_ERRORS),
        summary="Run the engines over an uploaded CSV, with no ground truth",
    )
    async def batch_predict_csv(
        request: Request,
        response: Response,
        file: UploadFile = File(..., description="A UTF-8 CSV with an `id` column."),
        k: int = Form(default=5, ge=MIN_K, le=MAX_K, description=K_DESCRIPTION),
        ensemble_method: str = Form(default="weighted", description=ENSEMBLE_DESCRIPTION),
        flag_radius_km: float = Form(
            default=ACC_KM_THRESHOLD,
            ge=MIN_FLAG_RADIUS_KM,
            le=MAX_FLAG_RADIUS_KM,
            description=FLAG_RADIUS_DESCRIPTION,
        ),
        engines_field: str = Form(
            default="", alias="engines", description=ENGINES_DESCRIPTION
        ),
        on_row_error: str = Form(default="fail", description=ON_ROW_ERROR_DESCRIPTION),
    ) -> BatchResponse:
        _check_form(k, ensemble_method, flag_radius_km, on_row_error)
        selected = _selected_engines(_parse_engines_field(engines_field))
        inputs = await _parse_csv_upload(file, on_row_error=on_row_error)
        inputs = _validate_inputs(inputs, on_row_error)
        _rate_limit("batches", request, response)
        return _run_and_view(
            inputs,
            k,
            with_eval=False,
            ensemble_method=ensemble_method,
            flag_radius_km=flag_radius_km,
            selected=selected,
            base_url=request_base_url(request),
        )

    @app.post(
        "/eval",
        response_model=BatchResponse,
        responses=dict(BATCH_ERRORS),
        summary="Run the engines over many labelled rows and report metrics",
    )
    def eval_batch(request: Request, response: Response, body: BatchRequest) -> BatchResponse:
        selected = _selected_engines(body.engines)
        inputs = [
            BatchInput(
                id=r.id,
                post=r.post,
                user_posts=r.user_posts,
                user_handle=r.user_handle,
                ground_truth_city=_clean_truth(r.ground_truth_city),
                ground_truth_user_city=_clean_truth(r.ground_truth_user_city),
                bucket=r.bucket,
                should_disagree=r.should_disagree,
            )
            for r in body.inputs
        ]
        _reject_duplicate_ids(inputs)
        _require_some_truth(
            inputs,
            "Give at least one row a ground_truth_city or a ground_truth_user_city.",
        )
        inputs = _validate_inputs(inputs, body.on_row_error)
        _rate_limit("batches", request, response)
        return _run_and_view(
            inputs,
            body.k,
            with_eval=True,
            ensemble_method=body.ensemble_method,
            flag_radius_km=body.flag_radius_km,
            selected=selected,
            base_url=request_base_url(request),
        )

    @app.post(
        "/eval_csv",
        response_model=BatchResponse,
        responses=dict(BATCH_ERRORS),
        summary="Run the engines over a labelled CSV and report metrics",
    )
    async def eval_csv(
        request: Request,
        response: Response,
        file: UploadFile = File(
            ...,
            description=(
                "A UTF-8 CSV with an `id` column and at least one of "
                "`ground_truth_city` or `ground_truth_user_city`."
            ),
        ),
        k: int = Form(default=5, ge=MIN_K, le=MAX_K, description=K_DESCRIPTION),
        ensemble_method: str = Form(default="weighted", description=ENSEMBLE_DESCRIPTION),
        flag_radius_km: float = Form(
            default=ACC_KM_THRESHOLD,
            ge=MIN_FLAG_RADIUS_KM,
            le=MAX_FLAG_RADIUS_KM,
            description=FLAG_RADIUS_DESCRIPTION,
        ),
        engines_field: str = Form(
            default="", alias="engines", description=ENGINES_DESCRIPTION
        ),
        on_row_error: str = Form(default="fail", description=ON_ROW_ERROR_DESCRIPTION),
    ) -> BatchResponse:
        _check_form(k, ensemble_method, flag_radius_km, on_row_error)
        selected = _selected_engines(_parse_engines_field(engines_field))
        inputs, headers = await _parse_csv_upload(
            file, allow_ground_truth=True, on_row_error=on_row_error, with_headers=True
        )
        if not (TRUTH_HEADERS & headers):
            raise ApiError(
                422,
                (
                    "The file has no ground-truth column, so /eval has nothing "
                    f"to score. Add {' or '.join(sorted(TRUTH_HEADERS))}, or "
                    "upload to /batch_predict_csv to run the engines without "
                    f"metrics. The file's columns are: {', '.join(sorted(headers))}."
                ),
                code="missing_truth_header",
                field="file",
            )
        _require_some_truth(
            inputs, "Every ground-truth cell in the file is empty."
        )
        inputs = _validate_inputs(inputs, on_row_error)
        _rate_limit("batches", request, response)
        return _run_and_view(
            inputs,
            k,
            with_eval=True,
            ensemble_method=ensemble_method,
            flag_radius_km=flag_radius_km,
            selected=selected,
            base_url=request_base_url(request),
        )

    async def _parse_csv_upload(
        file: UploadFile,
        allow_ground_truth: bool = False,
        on_row_error: str = "fail",
        with_headers: bool = False,
    ):
        """Read an uploaded CSV into batch rows, or say exactly what is wrong.

        Four guarantees. The byte-order mark Excel writes is dropped rather
        than landing on the first header. A missing `id` header is a refusal,
        never a row number silently substituted for an id. A duplicate or
        empty id is a refusal, because two result rows cannot share one key.
        And a truth value is normalised before it is matched, so "Bedok " is
        not scored as out of catalogue.
        """
        raw = await file.read()
        cap = max_batch_bytes()
        if len(raw) > cap:
            raise ApiError(
                413,
                f"CSV too large: {len(raw)} bytes (max {cap}).",
                code="upload_too_large",
                field="file",
            )
        try:
            # utf-8-sig, not utf-8: it reads a file with or without the mark
            # Excel writes, and drops the mark when it is there.
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ApiError(
                400, f"CSV must be UTF-8: {e}", code="not_utf8", field="file"
            ) from e
        # newline="" leaves the line endings to the csv module, which is what
        # lets a quoted cell hold a newline and a CRLF file parse as one row
        # per record.
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = {(h or "").strip() for h in (reader.fieldnames or [])}
        if "id" not in headers:
            raise ApiError(
                422,
                (
                    "The file has no `id` column. Every row needs a stable "
                    "identifier: the results are returned against it and an "
                    "export joins on it. The file's columns are: "
                    f"{', '.join(sorted(h for h in headers if h)) or '(none)'}."
                ),
                code="missing_id_header",
                field="file",
            )
        rows: list[BatchInput] = []
        for line, row in enumerate(reader, start=2):
            row_id = str(row.get("id") or "").strip()[:MAX_ROW_ID_CHARS]
            if not row_id:
                message = (
                    f"the `id` cell on line {line} is empty. Ids are never "
                    "substituted with a row number: a result that cannot be "
                    "joined back to its input is worse than a refusal"
                )
                if on_row_error == "fail":
                    raise ApiError(
                        422, message[0].upper() + message[1:], code="empty_row_id",
                        field="file",
                    )
                rows.append(BatchInput(id=f"(line {line})", error=message))
                continue
            user_posts_field = (row.get("user_posts") or "").strip()
            try:
                # The same bounds the JSON bodies get, so an oversized cell in
                # an uploaded file cannot reach the engines either.
                rows.append(
                    BatchInput(
                        id=row_id,
                        post=clean_post(row.get("post")),
                        user_handle=clean_handle(row.get("user_handle")),
                        user_posts=clean_timeline(user_posts_field.split("|")),
                        ground_truth_city=(
                            _clean_truth(row.get("ground_truth_city"))
                            if allow_ground_truth
                            else None
                        ),
                        ground_truth_user_city=(
                            _clean_truth(row.get("ground_truth_user_city"))
                            if allow_ground_truth
                            else None
                        ),
                        bucket=(row.get("bucket") or row.get("tag") or "").strip() or None,
                        should_disagree=_parse_bool(row.get("should_disagree")),
                    )
                )
            except InputError as e:
                if on_row_error == "fail":
                    raise ApiError(
                        422,
                        f"Row id={row_id!r}: {e}",
                        code="unusable_row",
                        field="file",
                        details=[{"field": "file", "id": row_id, "message": str(e)}],
                    ) from e
                rows.append(BatchInput(id=row_id, error=str(e)))
        _reject_duplicate_ids(rows)
        return (rows, headers) if with_headers else rows

    @app.get(
        "/",
        response_class=FileResponse,
        responses={
            200: {
                "description": "The single-page interface.",
                "content": {"text/html": {"schema": {"type": "string"}}},
            },
            **COMMON_ERRORS,
        },
        summary="The page",
    )
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def client_address(request: Request) -> str:
    """The address to rate-limit on.

    A client can put anything in X-Forwarded-For, so the header is read only
    as far as the proxies this instance is told to trust: each appends the
    peer it saw, so the address the last trusted proxy added is the
    `trusted_proxy_hops()`-th entry counting from the right. Anything further
    left was supplied by the client and is ignored.
    """
    direct = request.client.host if request.client else "unknown"
    hops_trusted = trusted_proxy_hops()
    if hops_trusted <= 0:
        return direct
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    index = len(hops) - hops_trusted
    if 0 <= index < len(hops):
        return hops[index]
    return direct


def _row_chars(row: BatchInput) -> int:
    """How much text one row carries, over every field an engine reads."""
    return len(row.id) + len(row.post or "") + sum(len(p) for p in row.user_posts or [])


def _handle_warning(ids: list[str]) -> list[str]:
    """Say so when the row ids read as account handles.

    A row id is returned on the result row and written into every export, so
    an id that is somebody's handle carries an identifier out of the run
    alongside a predicted location.
    """
    handles = [i for i in ids if HANDLE_PATTERN.match(i)]
    if not handles:
        return []
    shown = ", ".join(repr(h) for h in handles[:3])
    return [
        f"{len(handles)} row id(s) read as account handles ({shown}). A row id "
        "is echoed on the result row and written into every export, so it "
        "leaves this run beside a predicted location. Use a key of your own "
        "and keep the handle in your own file."
    ]


def _clean_truth(value: str | None) -> str | None:
    """Normalise a ground-truth place name, or None when the cell is empty.

    A truth is matched against the catalogue by name, so a trailing space
    made "Bedok " out of catalogue while "Bedok" was not.
    """
    if value is None:
        return None
    cleaned = normalise_text(str(value))
    return cleaned or None


def _reject_duplicate_ids(rows: list[BatchInput]) -> None:
    """Two rows with one id cannot both be found in the results."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        if row.id in seen and row.id not in duplicates:
            duplicates.append(row.id)
        seen.add(row.id)
    if duplicates:
        raise ApiError(
            422,
            (
                "Two or more rows share an id: "
                + ", ".join(repr(d) for d in duplicates[:5])
                + ". A result row is returned against its id, so duplicates "
                "cannot be told apart."
            ),
            code="duplicate_row_ids",
            field="inputs",
            details=[{"field": "inputs", "id": d} for d in duplicates[:20]],
        )


def _check_form(
    k: int, ensemble_method: str, flag_radius_km: float, on_row_error: str = "fail"
) -> None:
    """Range-check the multipart form fields of the CSV endpoints.

    Pydantic applies these bounds to the JSON bodies; a form does not go
    through a model, so the same checks are applied here.
    """
    try:
        check_k(k)
        check_fusion_method(ensemble_method)
    except InputError as e:
        raise ApiError(422, str(e), code="out_of_range") from e
    _check_flag_radius(flag_radius_km)
    if on_row_error not in ("fail", "skip"):
        raise ApiError(
            422,
            f'on_row_error must be "fail" or "skip"; got {on_row_error!r}.',
            code="out_of_range",
            field="on_row_error",
        )


def _check_flag_radius(value: float) -> None:
    """Range-check a radius that arrived as a form field.

    Pydantic does this for the JSON bodies; a multipart form does not go
    through a model, so the same bounds are applied here. NaN and the
    infinities fail every comparison, so they are refused by name.
    """
    if not math.isfinite(value):
        raise ApiError(
            422,
            f"flag_radius_km must be a finite number; got {value}.",
            code="out_of_range",
            field="flag_radius_km",
        )
    if not (MIN_FLAG_RADIUS_KM <= value <= MAX_FLAG_RADIUS_KM):
        raise ApiError(
            422,
            (
                f"flag_radius_km must be between {MIN_FLAG_RADIUS_KM:.0f} and "
                f"{MAX_FLAG_RADIUS_KM:.0f} km; got {value}."
            ),
            code="out_of_range",
            field="flag_radius_km",
        )


def _parse_bool(value: str | None) -> bool | None:
    """Parse an optional boolean CSV cell; None when blank/absent."""
    if value is None:
        return None
    v = value.strip().lower()
    if v == "":
        return None
    return v in {"1", "true", "yes", "y", "t"}


def _parse_engines_field(raw: str) -> list[str] | None:
    """Read the comma-separated `engines` field of a CSV upload form."""
    names = [n.strip() for n in (raw or "").split(",") if n.strip()]
    return names or None


def _check_place_scale(name: str) -> None:
    """Refuse a building or a street address when the deployment says to.

    A shared instance geolocates posts to neighbourhoods and towns. Asking it
    for one person's address is a different thing, and a deployment that is
    open to the public sets GEOLENS_REFUSE_ADDRESS_LIKE rather than warning.
    """
    reason = address_like_reason(name)
    if reason is None or not refuse_address_like():
        return
    raise ApiError(
        422,
        reason + " This instance onboards places, not addresses.",
        code="place_too_fine_grained",
        field="city",
    )


def _profile_to_dict(profile: CityProfile, catalogue: list[str] | None = None) -> dict:
    warnings = profile_warnings(profile)
    scale = address_like_reason(profile.name)
    if scale is not None:
        warnings.append(scale + " Onboard the neighbourhood or the town instead.")
    if catalogue is not None:
        near = proximity_warning(profile.name, profile.lat, profile.lon, catalogue)
        if near is not None:
            warnings.append(near)
    return {
        "name": profile.name,
        "place_id": place_id(profile.name),
        "aliases": profile.aliases,
        "landmarks": profile.landmarks,
        "foods": profile.foods,
        "slang": profile.slang,
        "notes": profile.notes,
        "lat": profile.lat,
        "lon": profile.lon,
        "region": profile.region,
        "source": profile.source,
        # What the operator should fix before adding the city (empty fields,
        # missing or implausible centroid, a centroid sitting on a place that
        # is already a candidate, stub fallback). Shown next to the cards.
        "warnings": warnings,
    }
