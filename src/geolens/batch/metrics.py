"""Evaluation metrics: Acc@1, Acc@5, mean rank, distance error, latency, cost.

Computed only over rows with `status=ok` AND a ground truth in the catalogue.
OOC and error rows are reported separately.

Beyond label accuracy we report the distance metrics geolocation work actually
uses (median/mean great-circle error in km, and Acc@161km), so a near-miss is
not penalised the same as a far-miss. See `geolens.geo`.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from geolens.batch.runner import BatchRow
from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._coords import coords_for
from geolens.geo import ACC_KM_THRESHOLD, haversine_km
from geolens.stats import wilson_interval


@dataclass
class CityCount:
    """How many rows a corpus places in a given city (the 'where is the
    conversation coming from' view), per granularity bucket."""

    city: str
    post_count: int = 0
    user_count: int = 0


def compute_rollup(rows: list[BatchRow]) -> list[CityCount]:
    """Aggregate predicted locations across a corpus into per-city counts.

    Unlike the accuracy summary this needs no ground truth, so it works on the
    plain batch-prediction path too. Sorted by total count, descending.
    """
    counts: dict[str, CityCount] = {}

    def _bump(city: str | None, bucket: str) -> None:
        if not city:
            return
        cc = counts.setdefault(city, CityCount(city=city))
        if bucket == "post":
            cc.post_count += 1
        else:
            cc.user_count += 1

    for r in rows:
        if r.status != "ok":
            continue
        post = r.ensembles.get("post")
        user = r.ensembles.get("user")
        _bump(post.consensus_city if post else None, "post")
        _bump(user.consensus_city if user else None, "user")
    return sorted(
        counts.values(), key=lambda c: (c.post_count + c.user_count), reverse=True
    )


@dataclass
class EngineMetrics:
    name: str
    acc_at_1: float = 0.0
    acc_at_5: float = 0.0
    # 95% Wilson score intervals for Acc@1 / Acc@5, so a reader does not
    # over-read a gap between two engines that is within sampling noise.
    acc_at_1_ci: tuple[float, float] = (0.0, 0.0)
    acc_at_5_ci: tuple[float, float] = (0.0, 0.0)
    mean_rank: float = 0.0  # rank of ground truth in top-k; len(top_k)+1 if absent
    # Distance error of the top-1 prediction vs. ground truth (geolocation
    # convention). median/mean over rows where both cities have coordinates;
    # acc_at_161km over rows with a known ground-truth coordinate (a top-1
    # prediction with no coordinate counts as outside the threshold).
    median_error_km: float = 0.0
    mean_error_km: float = 0.0
    acc_at_161km: float = 0.0
    n_geo: int = 0  # rows contributing to the distance metrics
    median_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    n_evaluated: int = 0


@dataclass
class EnsembleMetrics:
    granularity: str
    acc_at_1: float = 0.0
    acc_at_5: float = 0.0
    acc_at_1_ci: tuple[float, float] = (0.0, 0.0)
    mean_rank: float = 0.0
    median_error_km: float = 0.0
    acc_at_161km: float = 0.0
    n_evaluated: int = 0
    # How often the ensemble's top-1 differed from the best single engine in the bucket.
    differs_from_best_single_rate: float = 0.0


@dataclass
class BucketMetrics:
    """Per-difficulty-bucket Acc@1 for each engine, so the aggregate table can
    be read alongside where each method actually wins or fails."""

    bucket: str
    n_rows: int = 0
    acc_at_1: dict[str, float] = field(default_factory=dict)  # engine name -> Acc@1


@dataclass
class BannerMetrics:
    """Precision/recall of the cross-task disagreement banner against gold
    `should_disagree` labels, when the uploaded set provides them. Lets the
    banner's reliability be reported as a number rather than asserted."""

    n_labelled: int = 0
    n_positive: int = 0  # rows that should fire the banner
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    precision: float = 0.0
    recall: float = 0.0


@dataclass
class EvalSummary:
    total_rows: int = 0
    evaluated_rows: int = 0
    ooc_rows: int = 0
    error_rows: int = 0
    # Closed-set size: how many candidate cities each engine chooses among.
    # Acc@1 is uninterpretable without it.
    catalogue_size: int = 0
    per_engine: dict[str, EngineMetrics] = field(default_factory=dict)
    ensembles: dict[str, EnsembleMetrics] = field(default_factory=dict)
    per_bucket: dict[str, BucketMetrics] = field(default_factory=dict)
    banner: BannerMetrics | None = None


def _rank_in_topk(top_k: list[tuple[str, float]], target: str) -> int:
    """1-indexed rank of `target` in `top_k`. Returns len(top_k)+1 if absent."""
    target_l = target.lower()
    for i, (city, _) in enumerate(top_k, start=1):
        if city.lower() == target_l:
            return i
    return len(top_k) + 1


def _distance_eval(pred_city: str, gt_city: str) -> tuple[float | None, bool]:
    """Return (error_km, within_161km) for a top-1 prediction vs. ground truth.

    error_km is None when either city has no coordinate; within_161km is False
    in that case (an answer we cannot place on the map is not within 100 miles).
    """
    gt = coords_for(gt_city)
    if gt is None:
        return None, False
    pred = coords_for(pred_city)
    if pred is None:
        return None, False
    err = haversine_km(pred, gt)
    return err, err <= ACC_KM_THRESHOLD


def _summarise_distance(errors: list[float], within: list[bool]) -> tuple[float, float, float]:
    """median_error_km, mean_error_km (over known-coord rows), acc_at_161km (over `within`)."""
    median_km = statistics.median(errors) if errors else 0.0
    mean_km = sum(errors) / len(errors) if errors else 0.0
    acc161 = sum(1 for w in within if w) / len(within) if within else 0.0
    return median_km, mean_km, acc161


def bucket_of(row: BatchRow) -> str:
    """Difficulty bucket for a row: explicit `bucket`, else the id prefix
    (``hard-sem-3`` -> ``hard-sem``), else ``other``."""
    if row.bucket:
        return row.bucket
    m = re.match(r"^(.*?)-?\d+$", row.id or "")
    return (m.group(1) if m and m.group(1) else (row.id or "other")) or "other"


def compute_summary(rows: list[BatchRow], catalogue_size: int | None = None) -> EvalSummary:
    summary = EvalSummary(
        total_rows=len(rows),
        catalogue_size=catalogue_size if catalogue_size is not None else len(DEFAULT_CITIES),
    )
    summary.ooc_rows = sum(1 for r in rows if r.status == "ooc")
    summary.error_rows = sum(1 for r in rows if r.status == "error")

    eligible = [r for r in rows if r.status == "ok" and r.ground_truth_city]
    summary.evaluated_rows = len(eligible)
    if not eligible:
        return summary

    # Collect engine names from the first eligible row that has them.
    engine_names = list(eligible[0].per_engine.keys())

    for engine in engine_names:
        ranks: list[int] = []
        latencies: list[float] = []
        costs: list[float] = []
        errors: list[float] = []
        within: list[bool] = []
        hits1 = 0
        hits5 = 0
        n = 0
        for r in eligible:
            pred = r.per_engine.get(engine)
            if pred is None:
                continue
            n += 1
            rank = _rank_in_topk(pred.top_k, r.ground_truth_city or "")
            ranks.append(rank)
            if rank == 1:
                hits1 += 1
            if rank <= 5:
                hits5 += 1
            latencies.append(pred.latency_ms)
            costs.append(pred.cost_usd)
            err_km, is_within = _distance_eval(pred.city, r.ground_truth_city or "")
            if coords_for(r.ground_truth_city) is not None:
                within.append(is_within)
                if err_km is not None:
                    errors.append(err_km)
        if n == 0:
            continue
        median_km, mean_km, acc161 = _summarise_distance(errors, within)
        summary.per_engine[engine] = EngineMetrics(
            name=engine,
            acc_at_1=hits1 / n,
            acc_at_5=hits5 / n,
            acc_at_1_ci=wilson_interval(hits1, n),
            acc_at_5_ci=wilson_interval(hits5, n),
            mean_rank=sum(ranks) / n,
            median_error_km=median_km,
            mean_error_km=mean_km,
            acc_at_161km=acc161,
            n_geo=len(within),
            median_latency_ms=statistics.median(latencies) if latencies else 0.0,
            total_cost_usd=sum(costs),
            n_evaluated=n,
        )

    for granularity in ("post", "user"):
        ranks: list[int] = []
        errors: list[float] = []
        within: list[bool] = []
        hits1 = 0
        hits5 = 0
        differs = 0
        n = 0
        for r in eligible:
            er = r.ensembles.get(granularity)
            if er is None or not r.ground_truth_city:
                continue
            n += 1
            rank = _rank_in_topk(er.top_k, r.ground_truth_city)
            ranks.append(rank)
            if rank == 1:
                hits1 += 1
            if rank <= 5:
                hits5 += 1
            if er.differs_from_best_single:
                differs += 1
            err_km, is_within = _distance_eval(er.consensus_city, r.ground_truth_city)
            if coords_for(r.ground_truth_city) is not None:
                within.append(is_within)
                if err_km is not None:
                    errors.append(err_km)
        if n == 0:
            continue
        median_km, _mean_km, acc161 = _summarise_distance(errors, within)
        summary.ensembles[granularity] = EnsembleMetrics(
            granularity=granularity,
            acc_at_1=hits1 / n,
            acc_at_5=hits5 / n,
            acc_at_1_ci=wilson_interval(hits1, n),
            mean_rank=sum(ranks) / n,
            median_error_km=median_km,
            acc_at_161km=acc161,
            n_evaluated=n,
            differs_from_best_single_rate=differs / n,
        )

    # Per-difficulty-bucket Acc@1 per engine (stratified view).
    buckets: dict[str, list[BatchRow]] = defaultdict(list)
    for r in eligible:
        buckets[bucket_of(r)].append(r)
    for bname, brows in buckets.items():
        bm = BucketMetrics(bucket=bname, n_rows=len(brows))
        for engine in engine_names:
            hits = tot = 0
            for r in brows:
                pred = r.per_engine.get(engine)
                if pred is None:
                    continue
                tot += 1
                if _rank_in_topk(pred.top_k, r.ground_truth_city or "") == 1:
                    hits += 1
            if tot:
                bm.acc_at_1[engine] = hits / tot
        summary.per_bucket[bname] = bm

    # Cross-task disagreement banner precision/recall, when the upload tags
    # which rows should fire it (the bundled set tags osint-* / disagree-*).
    labelled = [
        r for r in eligible
        if r.should_disagree is not None and r.triangulation is not None
    ]
    if labelled:
        bm2 = BannerMetrics(n_labelled=len(labelled))
        for r in labelled:
            fired = bool(r.triangulation.disagreement_flag)
            gold = bool(r.should_disagree)
            bm2.n_positive += int(gold)
            if gold and fired:
                bm2.true_positive += 1
            elif fired and not gold:
                bm2.false_positive += 1
            elif gold and not fired:
                bm2.false_negative += 1
        tp, fp, fn = bm2.true_positive, bm2.false_positive, bm2.false_negative
        bm2.precision = tp / (tp + fp) if (tp + fp) else 0.0
        bm2.recall = tp / (tp + fn) if (tp + fn) else 0.0
        summary.banner = bm2

    return summary
