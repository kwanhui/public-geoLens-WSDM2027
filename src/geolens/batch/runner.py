"""Batch executor: run a list of inputs through every engine + the ensembles.

Used by both `/batch_predict` (no ground truth) and `/eval` (with ground truth).
The runner is engine-agnostic: it takes a dict of `{name: Engine}` from the
caller, so the same code path serves the live workbench and any future
extensions (e.g., a smaller engine subset for faster eval runs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from geolens.engines.base import Engine, GeolocateInput, Prediction
from geolens.ensemble import EnsembleResult, ensemble
from geolens.triangulator import TriangulationResult, triangulate

Granularity = Literal["post", "user"]
RowStatus = Literal["ok", "error", "ooc"]


@dataclass
class BatchInput:
    """One row from a bulk upload."""

    id: str
    post: str | None = None
    user_posts: list[str] | None = None
    user_handle: str | None = None
    ground_truth_city: str | None = None
    # Optional difficulty/category label for stratified metrics. When absent it
    # is derived from the id prefix (e.g. "hard-sem-3" -> "hard-sem").
    bucket: str | None = None
    # Optional gold label for the cross-task disagreement banner: True if this
    # row should fire it (post/user genuinely conflict), False if not. Lets the
    # eval endpoint report the banner's precision and recall.
    should_disagree: bool | None = None


@dataclass
class BatchRow:
    """One row's results from the runner."""

    id: str
    status: RowStatus
    in_catalogue: bool
    ground_truth_city: str | None
    bucket: str | None = None
    should_disagree: bool | None = None
    per_engine: dict[str, Prediction] = field(default_factory=dict)
    ensembles: dict[str, EnsembleResult] = field(default_factory=dict)
    triangulation: TriangulationResult | None = None
    error: str | None = None


def run_batch(
    inputs: list[BatchInput],
    engines: dict[str, Engine],
    *,
    catalogue: list[str] | None = None,
    k: int = 5,
    ensemble_method: str = "weighted",
) -> list[BatchRow]:
    """Run all engines + ensembles on every input row.

    `catalogue` is the city set used to flag OOC ground-truth rows. If None,
    every row is treated as in-catalogue (status='ok' even if ground_truth
    isn't in any engine's classnames).
    """

    granularities = {n: e.granularity for n, e in engines.items()}
    catalogue_set = {c.lower() for c in (catalogue or [])}
    rows: list[BatchRow] = []

    for inp in inputs:
        gt_in_cat = (
            inp.ground_truth_city is None
            or not catalogue
            or inp.ground_truth_city.lower() in catalogue_set
        )
        if inp.ground_truth_city and not gt_in_cat:
            rows.append(
                BatchRow(
                    id=inp.id,
                    status="ooc",
                    in_catalogue=False,
                    ground_truth_city=inp.ground_truth_city,
                    bucket=inp.bucket,
                    should_disagree=inp.should_disagree,
                )
            )
            continue

        try:
            payload = GeolocateInput(
                post=inp.post,
                user_posts=inp.user_posts,
                user_handle=inp.user_handle,
            )
            per_engine = {n: e.predict(payload, k=k) for n, e in engines.items()}
            tri = triangulate(per_engine, engines=granularities)
            ens: dict[str, EnsembleResult] = {}
            for target in ("post", "user"):
                er = ensemble(per_engine, granularities, target=target, k=k, method=ensemble_method)
                if er is not None:
                    ens[target] = er
            rows.append(
                BatchRow(
                    id=inp.id,
                    status="ok",
                    in_catalogue=True,
                    ground_truth_city=inp.ground_truth_city,
                    bucket=inp.bucket,
                    should_disagree=inp.should_disagree,
                    per_engine=per_engine,
                    ensembles=ens,
                    triangulation=tri,
                )
            )
        except Exception as e:  # noqa: BLE001
            rows.append(
                BatchRow(
                    id=inp.id,
                    status="error",
                    in_catalogue=gt_in_cat,
                    ground_truth_city=inp.ground_truth_city,
                    bucket=inp.bucket,
                    should_disagree=inp.should_disagree,
                    error=str(e),
                )
            )

    return rows
