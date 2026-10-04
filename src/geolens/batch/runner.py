"""Run a list of inputs through every engine and the ensembles.

Used by both `/batch_predict` (no ground truth) and `/eval` (with ground
truth). The runner takes a dict of `{name: Engine}` from the caller rather
than building the roster itself.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal

from geolens.dispatch import run_engines
from geolens.engines.base import Engine, GeolocateInput, Prediction
from geolens.ensemble import EnsembleResult, as_fusion_method, ensemble
from geolens.geo import ACC_KM_THRESHOLD
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
    # Optional home city for the account behind `user_posts`. When present the
    # user-level engines and the user-level fusion are scored against it and
    # the post-level ones against `ground_truth_city`. A row whose post and
    # user truths differ cannot be scored correctly at both levels otherwise,
    # which is why `eval/adapters/run_wnut_eval.py` scores each level
    # separately.
    ground_truth_user_city: str | None = None
    # Optional difficulty/category label for stratified metrics. When absent it
    # is derived from the id prefix (e.g. "hard-sem-3" -> "hard-sem").
    bucket: str | None = None
    # Optional gold label for the cross-task disagreement banner: True if this
    # row should fire it (post/user genuinely conflict), False if not. Lets the
    # eval endpoint report the banner's precision and recall.
    should_disagree: bool | None = None
    # Why this row cannot be run, set before the runner sees it. A row that
    # carries one is returned with status "error" and no engine is called for
    # it, which is what `on_row_error: "skip"` does with a row the caller
    # would otherwise have lost the whole batch to.
    error: str | None = None


@dataclass
class BatchRow:
    """One row's results from the runner."""

    id: str
    status: RowStatus
    in_catalogue: bool
    ground_truth_city: str | None
    ground_truth_user_city: str | None = None
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
    flag_radius_km: float = ACC_KM_THRESHOLD,
    selected: Collection[str] | None = None,
) -> list[BatchRow]:
    """Run all engines + ensembles on every input row.

    `catalogue` is the city set used to flag OOC ground-truth rows. If None,
    every row is treated as in-catalogue (status='ok' even if ground_truth
    isn't in any engine's classnames).

    `flag_radius_km` is the separation at which the verification flag is
    raised, defaulting to the 161 km convention.

    `selected` names the engines to run; None runs them all, and an engine
    left out comes back marked not selected rather than missing.
    """

    granularities = {n: e.granularity for n, e in engines.items()}
    catalogue_set = {c.lower() for c in (catalogue or [])}
    rows: list[BatchRow] = []

    def _in_catalogue(city: str | None) -> bool:
        return city is None or not catalogue or city.lower() in catalogue_set

    for inp in inputs:
        # A row the caller's parser already rejected. No engine is called for
        # it and it comes back beside the results rather than costing the run.
        if inp.error is not None:
            rows.append(
                BatchRow(
                    id=inp.id,
                    status="error",
                    in_catalogue=True,
                    ground_truth_city=inp.ground_truth_city,
                    ground_truth_user_city=inp.ground_truth_user_city,
                    bucket=inp.bucket,
                    should_disagree=inp.should_disagree,
                    error=inp.error,
                )
            )
            continue

        # A row is out of catalogue when either supplied truth is, because
        # neither level could ever return it.
        gt_in_cat = _in_catalogue(inp.ground_truth_city) and _in_catalogue(
            inp.ground_truth_user_city
        )
        if (inp.ground_truth_city or inp.ground_truth_user_city) and not gt_in_cat:
            rows.append(
                BatchRow(
                    id=inp.id,
                    status="ooc",
                    in_catalogue=False,
                    ground_truth_city=inp.ground_truth_city,
                    ground_truth_user_city=inp.ground_truth_user_city,
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
            per_engine = run_engines(engines, payload, k=k, selected=selected)
            tri = triangulate(per_engine, engines=granularities, radius_km=flag_radius_km)
            ens: dict[str, EnsembleResult] = {}
            for target in ("post", "user"):
                er = ensemble(per_engine, granularities, target=target, k=k,
                              method=as_fusion_method(ensemble_method))
                if er is not None:
                    ens[target] = er
            rows.append(
                BatchRow(
                    id=inp.id,
                    status="ok",
                    in_catalogue=True,
                    ground_truth_city=inp.ground_truth_city,
                    ground_truth_user_city=inp.ground_truth_user_city,
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
                    ground_truth_user_city=inp.ground_truth_user_city,
                    bucket=inp.bucket,
                    should_disagree=inp.should_disagree,
                    error=str(e),
                )
            )

    return rows
