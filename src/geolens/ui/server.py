"""FastAPI routes for the GeoLens demo.

Endpoints:
- GET  /healthz        liveness check (no auth, no LLM)
- POST /geolocate      run all engines on one input, return per-engine + triangulation
- POST /onboard        onboard a new city via the cold-start wizard
- POST /batch_predict  bulk inference (no ground truth) on a JSON or CSV payload
- POST /eval           bulk inference with ground truth; returns metrics summary
- GET  /               serve the static map UI

Cost-guards (per-IP rate limit and per-session USD cap) read MAX_QUERIES_PER_HOUR
and MAX_USD_PER_SESSION from the environment.
Batch endpoints additionally cap rows per request and have their own per-IP
batch-rate limit so a public Space cannot be drained by repeated bulk calls.
"""

from __future__ import annotations

import csv
import io
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from geolens.batch import BatchInput, compute_summary, run_batch
from geolens.batch.metrics import compute_rollup
from geolens.engines import (
    ClaudeClassifierEngine,
    ContrastGeoEngine,
    FewUserEngine,
    GazetteerEngine,
    LLMClassifierEngine,
    RetrieveZeroEngine,
)
from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines.base import GeolocateInput
from geolens.ensemble import ensemble
from geolens.manifest import build_manifest
from geolens.onboarding import CityProfile, onboard_city, profile_warnings, save_profile
from geolens.triangulator import triangulate

STATIC_DIR = Path(__file__).parent / "static"

MAX_QUERIES_PER_HOUR = int(os.getenv("MAX_QUERIES_PER_HOUR", "30"))
MAX_BATCH_ROWS = int(os.getenv("MAX_BATCH_ROWS", "50"))
MAX_BATCH_BYTES = int(os.getenv("MAX_BATCH_BYTES", "200000"))  # ~200 KB CSV
MAX_BATCHES_PER_HOUR = int(os.getenv("MAX_BATCHES_PER_HOUR", "5"))


def _build_engines() -> tuple[dict, list[str]]:
    """The full workbench roster, plus the shared live catalogue.

    Engines are keyed by display name; the granularity of each is read off
    the `Engine.granularity` property by the server. Cheap engines first
    (gazetteer, encoder) so the UI can show partial results before the
    expensive LLM calls land.

    Every engine is handed the *same* mutable catalogue list. Appending a
    newly onboarded city to that list makes it visible to all engines on the
    next request (the gazetteer matches its aliases, the LLM classifiers add
    it to the prompt, and the encoders re-embed it because their cache key is
    keyed on the city set), with no rebuild or restart.
    """
    catalogue = list(DEFAULT_CITIES)
    engines = {
        "contrastgeo": ContrastGeoEngine(cities=catalogue),
        "fewuser": FewUserEngine(cities=catalogue),
        "retrievezero": RetrieveZeroEngine(cities=catalogue),
        "gazetteer_post": GazetteerEngine(granularity="post", cities=catalogue),
        "gazetteer_user": GazetteerEngine(granularity="user", cities=catalogue),
        "gpt4o_mini_post": LLMClassifierEngine(granularity="post", cities=catalogue),
        "gpt4o_mini_user": LLMClassifierEngine(granularity="user", cities=catalogue),
        "claude_haiku_post": ClaudeClassifierEngine(granularity="post", cities=catalogue),
        "claude_haiku_user": ClaudeClassifierEngine(granularity="user", cities=catalogue),
    }
    return engines, catalogue


# ----- Request / response models ---------------------------------------------

class GeolocateRequest(BaseModel):
    post: str | None = Field(default=None, description="Single post text")
    user_handle: str | None = None
    user_posts: list[str] | None = None
    k: int = 5
    ensemble_method: str = Field(default="weighted", description='"weighted" or "rrf"')


class EnginePrediction(BaseModel):
    city: str
    confidence: float
    top_k: list[tuple[str, float]]
    latency_ms: float
    cost_usd: float
    note: str
    mode: str = "real"  # "real" | "stub"
    abstain: bool = False
    evidence: str = ""


class TriangulationView(BaseModel):
    consensus_city: str
    consensus_confidence: float
    agreement_score: float
    disagreement_flag: bool
    post_consensus_city: str = ""
    user_consensus_city: str = ""
    disagreement_km: float | None = None
    disagreement_score: float = 0.0
    notes: list[str]


class EnsembleView(BaseModel):
    granularity: str
    consensus_city: str
    consensus_confidence: float
    method: str = "weighted"
    top_k: list[tuple[str, float]]
    contributing_engines: list[str]
    best_single_engine: str
    best_single_city: str
    best_single_confidence: float
    delta_vs_best_single: float
    differs_from_best_single: bool


class GeolocateResponse(BaseModel):
    per_engine: dict[str, EnginePrediction]
    triangulation: TriangulationView
    ensembles: dict[str, EnsembleView] = {}
    manifest: dict = {}  # run metadata for provenance (models, catalogue, k, method)


class OnboardRequest(BaseModel):
    city: str
    force_refresh: bool = False


class SaveProfileRequest(BaseModel):
    name: str
    aliases: list[str] = []
    landmarks: list[str] = []
    foods: list[str] = []
    slang: list[str] = []
    notes: str = ""
    lat: float | None = None
    lon: float | None = None


# ----- Batch I/O models ------------------------------------------------------

class BatchInputRow(BaseModel):
    id: str
    post: str | None = None
    user_handle: str | None = None
    user_posts: list[str] | None = None
    ground_truth_city: str | None = None
    bucket: str | None = None
    should_disagree: bool | None = None


class BatchRequest(BaseModel):
    inputs: list[BatchInputRow]
    k: int = 5
    ensemble_method: str = "weighted"


class BatchPredictionRow(BaseModel):
    id: str
    status: str
    in_catalogue: bool
    ground_truth_city: str | None
    per_engine: dict[str, EnginePrediction] = {}
    ensembles: dict[str, EnsembleView] = {}
    triangulation: TriangulationView | None = None
    error: str | None = None


class EngineMetricsView(BaseModel):
    name: str
    acc_at_1: float
    acc_at_5: float
    acc_at_1_ci: tuple[float, float]
    acc_at_5_ci: tuple[float, float]
    mean_rank: float
    median_error_km: float
    mean_error_km: float
    acc_at_161km: float
    n_geo: int
    median_latency_ms: float
    total_cost_usd: float
    n_evaluated: int


class EnsembleMetricsView(BaseModel):
    granularity: str
    acc_at_1: float
    acc_at_5: float
    acc_at_1_ci: tuple[float, float]
    mean_rank: float
    median_error_km: float
    acc_at_161km: float
    n_evaluated: int
    differs_from_best_single_rate: float


class BucketMetricsView(BaseModel):
    bucket: str
    n_rows: int
    acc_at_1: dict[str, float] = {}


class BannerMetricsView(BaseModel):
    n_labelled: int
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


class CityCountView(BaseModel):
    city: str
    post_count: int
    user_count: int


class BatchResponse(BaseModel):
    rows: list[BatchPredictionRow]
    summary: EvalSummaryView | None = None  # only set for /eval; null for /batch_predict
    rollup: list[CityCountView] = []  # per-city predicted-location counts (no ground truth needed)
    manifest: dict = {}  # run metadata for reproducibility (models, catalogue, k, method)


# ----- App -------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="GeoLens", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    engines, catalogue = _build_engines()
    granularities = {name: e.granularity for name, e in engines.items()}

    def _register_city(name: str) -> None:
        """Add an onboarded city to the live catalogue so every engine sees it."""
        if name and name not in catalogue:
            catalogue.append(name)
    rate_buckets: dict[str, deque[float]] = defaultdict(deque)
    batch_buckets: dict[str, deque[float]] = defaultdict(deque)

    def _rate_limit_check(request: Request) -> None:
        if MAX_QUERIES_PER_HOUR <= 0:
            return
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = rate_buckets[ip]
        cutoff = now - 3600
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= MAX_QUERIES_PER_HOUR:
            retry_after = max(1, int(bucket[0] + 3600 - now) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"Per-IP rate limit reached ({MAX_QUERIES_PER_HOUR} queries/hour).",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

    def _batch_rate_limit_check(request: Request) -> None:
        if MAX_BATCHES_PER_HOUR <= 0:
            return
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = batch_buckets[ip]
        cutoff = now - 3600
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= MAX_BATCHES_PER_HOUR:
            retry_after = max(1, int(bucket[0] + 3600 - now) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"Per-IP batch limit reached ({MAX_BATCHES_PER_HOUR} batches/hour).",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/geolocate", response_model=GeolocateResponse)
    def geolocate(req: GeolocateRequest, request: Request) -> GeolocateResponse:
        _rate_limit_check(request)
        if not req.post and not req.user_posts and not req.user_handle:
            raise HTTPException(
                status_code=400,
                detail="Provide at least one of: post, user_handle, user_posts.",
            )
        payload = GeolocateInput(
            post=req.post,
            user_handle=req.user_handle,
            user_posts=req.user_posts,
        )
        per_engine = {name: e.predict(payload, k=req.k) for name, e in engines.items()}
        tri = triangulate(per_engine, engines=granularities)

        ensembles: dict[str, EnsembleView] = {}
        for target in ("post", "user"):
            er = ensemble(per_engine, granularities, target=target, k=req.k, method=req.ensemble_method)
            if er is None:
                continue
            ensembles[target] = EnsembleView(
                granularity=er.granularity,
                consensus_city=er.consensus_city,
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

        return GeolocateResponse(
            per_engine={
                n: EnginePrediction(**{
                    "city": p.city,
                    "confidence": p.confidence,
                    "top_k": p.top_k,
                    "latency_ms": p.latency_ms,
                    "cost_usd": p.cost_usd,
                    "note": p.note,
                    "mode": p.mode,
                    "abstain": p.abstain,
                    "evidence": p.evidence,
                })
                for n, p in per_engine.items()
            },
            triangulation=TriangulationView(
                consensus_city=tri.consensus_city,
                consensus_confidence=tri.consensus_confidence,
                agreement_score=tri.agreement_score,
                disagreement_flag=tri.disagreement_flag,
                post_consensus_city=tri.post_consensus_city,
                user_consensus_city=tri.user_consensus_city,
                disagreement_km=tri.disagreement_km,
                disagreement_score=tri.disagreement_score,
                notes=tri.notes,
            ),
            ensembles=ensembles,
            manifest=build_manifest(engines, catalogue, k=req.k, ensemble_method=req.ensemble_method),
        )

    @app.post("/onboard")
    def onboard(req: OnboardRequest, request: Request) -> dict:
        _rate_limit_check(request)
        profile = onboard_city(req.city, force_refresh=req.force_refresh)
        _register_city(profile.name)
        return _profile_to_dict(profile)

    @app.put("/onboard")
    def save_onboarded(req: SaveProfileRequest, request: Request) -> dict:
        """Persist an edited MoR profile (the operator's edits in the UI)."""
        _rate_limit_check(request)
        profile = CityProfile(
            name=req.name,
            aliases=req.aliases,
            landmarks=req.landmarks,
            foods=req.foods,
            slang=req.slang,
            notes=req.notes,
            lat=req.lat,
            lon=req.lon,
            source="edited",
        )
        saved = save_profile(profile)
        _register_city(saved.name)
        return _profile_to_dict(saved)

    # ----- Batch endpoints ---------------------------------------------------

    def _run_and_view(
        rows_in: list[BatchInput],
        k: int,
        with_eval: bool,
        ensemble_method: str = "weighted",
    ) -> BatchResponse:
        results = run_batch(
            rows_in, engines, catalogue=catalogue, k=k, ensemble_method=ensemble_method
        )
        rows_out: list[BatchPredictionRow] = []
        for r in results:
            per_engine_view = {
                n: EnginePrediction(
                    city=p.city,
                    confidence=p.confidence,
                    top_k=p.top_k,
                    latency_ms=p.latency_ms,
                    cost_usd=p.cost_usd,
                    note=p.note,
                    mode=p.mode,
                    abstain=p.abstain,
                    evidence=p.evidence,
                )
                for n, p in r.per_engine.items()
            }
            ens_view: dict[str, EnsembleView] = {}
            for g, er in r.ensembles.items():
                ens_view[g] = EnsembleView(
                    granularity=er.granularity,
                    consensus_city=er.consensus_city,
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
            tri_view = None
            if r.triangulation is not None:
                tri_view = TriangulationView(
                    consensus_city=r.triangulation.consensus_city,
                    consensus_confidence=r.triangulation.consensus_confidence,
                    agreement_score=r.triangulation.agreement_score,
                    disagreement_flag=r.triangulation.disagreement_flag,
                    post_consensus_city=r.triangulation.post_consensus_city,
                    user_consensus_city=r.triangulation.user_consensus_city,
                    disagreement_km=r.triangulation.disagreement_km,
                    disagreement_score=r.triangulation.disagreement_score,
                    notes=r.triangulation.notes,
                )
            rows_out.append(
                BatchPredictionRow(
                    id=r.id,
                    status=r.status,
                    in_catalogue=r.in_catalogue,
                    ground_truth_city=r.ground_truth_city,
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
            s = compute_summary(results, catalogue_size=len(catalogue))
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
                        median_error_km=m.median_error_km,
                        mean_error_km=m.mean_error_km,
                        acc_at_161km=m.acc_at_161km,
                        n_geo=m.n_geo,
                        median_latency_ms=m.median_latency_ms,
                        total_cost_usd=m.total_cost_usd,
                        n_evaluated=m.n_evaluated,
                    )
                    for n, m in s.per_engine.items()
                },
                ensembles={
                    g: EnsembleMetricsView(
                        granularity=m.granularity,
                        acc_at_1=m.acc_at_1,
                        acc_at_5=m.acc_at_5,
                        acc_at_1_ci=m.acc_at_1_ci,
                        mean_rank=m.mean_rank,
                        median_error_km=m.median_error_km,
                        acc_at_161km=m.acc_at_161km,
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
            engines, catalogue, k=k, ensemble_method=ensemble_method
        )
        rollup = [
            CityCountView(city=c.city, post_count=c.post_count, user_count=c.user_count)
            for c in compute_rollup(results)
        ]
        return BatchResponse(rows=rows_out, summary=summary_view, rollup=rollup, manifest=manifest)

    def _validate_inputs(inputs: list[BatchInput]) -> None:
        if not inputs:
            raise HTTPException(status_code=400, detail="No input rows.")
        if len(inputs) > MAX_BATCH_ROWS:
            raise HTTPException(
                status_code=413,
                detail=f"Batch too large: {len(inputs)} rows (max {MAX_BATCH_ROWS}).",
            )
        for r in inputs:
            if not (r.post or r.user_posts or r.user_handle):
                raise HTTPException(
                    status_code=400,
                    detail=f"Row id={r.id!r} has no post / user_posts / user_handle.",
                )

    @app.post("/batch_predict", response_model=BatchResponse)
    def batch_predict(request: Request, body: BatchRequest) -> BatchResponse:
        _batch_rate_limit_check(request)
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
        _validate_inputs(inputs)
        return _run_and_view(inputs, body.k, with_eval=False, ensemble_method=body.ensemble_method)

    @app.post("/batch_predict_csv", response_model=BatchResponse)
    async def batch_predict_csv(
        request: Request,
        file: UploadFile = File(...),
        k: int = Form(default=5),
        ensemble_method: str = Form(default="weighted"),
    ) -> BatchResponse:
        _batch_rate_limit_check(request)
        inputs = await _parse_csv_upload(file)
        _validate_inputs(inputs)
        return _run_and_view(inputs, k, with_eval=False, ensemble_method=ensemble_method)

    @app.post("/eval", response_model=BatchResponse)
    def eval_batch(request: Request, body: BatchRequest) -> BatchResponse:
        _batch_rate_limit_check(request)
        inputs = [
            BatchInput(
                id=r.id,
                post=r.post,
                user_posts=r.user_posts,
                user_handle=r.user_handle,
                ground_truth_city=r.ground_truth_city,
                bucket=r.bucket,
                should_disagree=r.should_disagree,
            )
            for r in body.inputs
        ]
        _validate_inputs(inputs)
        return _run_and_view(inputs, body.k, with_eval=True, ensemble_method=body.ensemble_method)

    @app.post("/eval_csv", response_model=BatchResponse)
    async def eval_csv(
        request: Request,
        file: UploadFile = File(...),
        k: int = Form(default=5),
        ensemble_method: str = Form(default="weighted"),
    ) -> BatchResponse:
        _batch_rate_limit_check(request)
        inputs = await _parse_csv_upload(file, allow_ground_truth=True)
        _validate_inputs(inputs)
        return _run_and_view(inputs, k, with_eval=True, ensemble_method=ensemble_method)

    async def _parse_csv_upload(
        file: UploadFile, allow_ground_truth: bool = False
    ) -> list[BatchInput]:
        raw = await file.read()
        if len(raw) > MAX_BATCH_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"CSV too large: {len(raw)} bytes (max {MAX_BATCH_BYTES}).",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise HTTPException(status_code=400, detail=f"CSV must be UTF-8: {e}") from e
        reader = csv.DictReader(io.StringIO(text))
        rows: list[BatchInput] = []
        for i, row in enumerate(reader, start=1):
            user_posts_field = (row.get("user_posts") or "").strip()
            user_posts = [s.strip() for s in user_posts_field.split("|") if s.strip()] or None
            rows.append(
                BatchInput(
                    id=str(row.get("id") or i),
                    post=(row.get("post") or "").strip() or None,
                    user_handle=(row.get("user_handle") or "").strip() or None,
                    user_posts=user_posts,
                    ground_truth_city=(
                        (row.get("ground_truth_city") or "").strip() or None
                        if allow_ground_truth
                        else None
                    ),
                    bucket=(row.get("bucket") or row.get("tag") or "").strip() or None,
                    should_disagree=_parse_bool(row.get("should_disagree")),
                )
            )
        return rows

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def _parse_bool(value: str | None) -> bool | None:
    """Parse an optional boolean CSV cell; None when blank/absent."""
    if value is None:
        return None
    v = value.strip().lower()
    if v == "":
        return None
    return v in {"1", "true", "yes", "y", "t"}


def _profile_to_dict(profile: CityProfile) -> dict:
    return {
        "name": profile.name,
        "aliases": profile.aliases,
        "landmarks": profile.landmarks,
        "foods": profile.foods,
        "slang": profile.slang,
        "notes": profile.notes,
        "lat": profile.lat,
        "lon": profile.lon,
        "source": profile.source,
        # What the operator should fix before adding the city (empty fields,
        # missing/implausible centroid, stub fallback). Shown next to the cards.
        "warnings": profile_warnings(profile),
    }
